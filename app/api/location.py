from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from app.ai.contracts import VisionUnavailable
from app.api.deps import (
    CurrentSession,
    Db,
    Maps,
    VisionDep,
    advance,
    check_map_version,
    check_revision,
    map_of,
    read_jpeg,
    status_of,
)
from app.config import get_settings
from app.domain.localization import Hints, match_location, retake_hint
from app.domain.session_state import State, Trigger, allows
from app.errors import ApiError, ErrorCode
from app.models import LocalizationRow
from app.schemas import ConfirmationIn, ConfirmationOut, ConfirmedLocation, LocalizationOut
from app.security import new_id

router = APIRouter(tags=["location"])


@router.post("/sessions/{session_id}/localizations")
def localize(
    row: CurrentSession,
    db: Db,
    maps: Maps,
    vision: VisionDep,
    image: Annotated[UploadFile, File(description="JPEG, ≤2 MB. Never stored.")],
    map_version: Annotated[str, Form()],
    floor_hint: Annotated[str | None, Form()] = None,
    last_node_id: Annotated[str | None, Form()] = None,
) -> LocalizationOut:
    """Photo → location candidates with evidence. Never confirms a location by itself (NFR-02)."""
    map_ = map_of(row, maps)
    check_map_version(map_, map_version)
    if not allows(State(row.state), Trigger.NO_MATCH):
        # Checked before calling the vision model so a wrong-state request costs nothing.
        raise ApiError(ErrorCode.INVALID_STATE, f"Not allowed while the session is {row.state}.", {"state": row.state})

    photo = read_jpeg(image)
    try:
        features = vision.extract(photo)
    except VisionUnavailable as exc:
        raise ApiError(ErrorCode.AI_SERVICE_UNAVAILABLE, str(exc)) from exc

    candidates = match_location(features, map_, Hints(floor_hint, last_node_id), row.language)
    ttl = get_settings().localization_ttl_seconds
    localization = LocalizationRow(
        id=new_id("loc"),
        session_id=row.id,
        status="needs_confirmation" if candidates else "no_match",
        candidates=[c.model_dump() for c in candidates],
        expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
    )
    db.add(localization)
    advance(row, Trigger.CANDIDATES_FOUND if candidates else Trigger.NO_MATCH)

    return LocalizationOut(
        localization_id=localization.id,
        status=localization.status,
        map_version=map_.version,
        expires_in_s=ttl,
        candidates=candidates,
        retake_hint=None if candidates else retake_hint(features.image_quality, row.language),
        next_action="confirm_location" if candidates else "recapture",
        session=status_of(row),
    )


@router.post("/sessions/{session_id}/location-confirmations")
def confirm_location(body: ConfirmationIn, row: CurrentSession, db: Db, maps: Maps) -> ConfirmationOut:
    """The user picks a photo candidate or chooses their spot from a list; only then is a location set."""
    check_revision(row, body.expected_revision)
    map_ = map_of(row, maps)
    node = map_.node_by_id.get(body.selected_node_id)
    if node is None:
        raise ApiError(ErrorCode.INVALID_INPUT, f"Unknown node {body.selected_node_id}.")

    if body.method == "photo_candidate":
        localization = db.get(LocalizationRow, body.localization_id)
        if localization is None or localization.session_id != row.id:
            raise ApiError(ErrorCode.LOCALIZATION_NOT_FOUND, "Unknown localization_id.")
        if localization.expires_at < datetime.now(UTC):
            raise ApiError(ErrorCode.LOCALIZATION_EXPIRED, "The photo result expired. Take a new photo.")
        if node.id not in {c["node_id"] for c in localization.candidates}:
            raise ApiError(ErrorCode.INVALID_INPUT, "selected_node_id is not one of the candidates.")

    advance(row, Trigger.LOCATION_CONFIRMED)
    row.confirmed_node_id = node.id
    row.confirmed_heading_deg = body.selected_heading_deg
    return ConfirmationOut(
        location=ConfirmedLocation(
            map_id=map_.map_id,
            floor_id=node.floor_id,
            node_id=node.id,
            heading_deg=body.selected_heading_deg,
            method=body.method,
        ),
        session=status_of(row),
    )
