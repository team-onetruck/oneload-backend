"""Dependencies and helpers shared by the endpoints: auth, revision checks, state transitions."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, Path, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.ai.contracts import Agent, VisionExtractor
from app.config import get_settings
from app.db import get_db
from app.domain.mapdata import MapData, MapStore
from app.domain.session_state import InvalidTransition, State, Trigger, transition
from app.errors import ApiError, ErrorCode
from app.models import SessionRow
from app.schemas import SessionStatus
from app.security import sha256_hex

Db = Annotated[Session, Depends(get_db)]


def get_maps(request: Request) -> MapStore:
    return request.app.state.maps


def get_agent(request: Request) -> Agent:
    return request.app.state.agent


def get_vision(request: Request) -> VisionExtractor:
    return request.app.state.vision


Maps = Annotated[MapStore, Depends(get_maps)]
AgentDep = Annotated[Agent, Depends(get_agent)]
VisionDep = Annotated[VisionExtractor, Depends(get_vision)]


def authorized_session(
    db: Db,
    session_id: Annotated[str, Path()],
    authorization: Annotated[str | None, Header()] = None,
) -> SessionRow:
    """Resolve the bearer token to its session and lock the row for the rest of the request.

    The row lock serialises concurrent requests on one session, so revision checks and state
    transitions never interleave.
    """
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not token:
        raise ApiError(ErrorCode.SESSION_UNAUTHORIZED, "A session token is required.")
    row = db.scalar(select(SessionRow).where(SessionRow.token_hash == sha256_hex(token)).with_for_update())
    if row is None:
        raise ApiError(ErrorCode.SESSION_UNAUTHORIZED, "The session token is not valid.")
    if row.id != session_id:
        raise ApiError(ErrorCode.SESSION_FORBIDDEN, "The token belongs to a different session.")

    now = datetime.now(UTC)
    if now - row.last_seen_at > timedelta(seconds=get_settings().session_idle_seconds):
        raise ApiError(ErrorCode.SESSION_EXPIRED, "The session expired. Create a new one.")
    row.last_seen_at = now
    return row


CurrentSession = Annotated[SessionRow, Depends(authorized_session)]


def map_of(row: SessionRow, maps: MapStore) -> MapData:
    map_ = maps.get(row.map_id)
    if map_ is None or map_.version != row.map_version:
        # The server's map moved on since this session started; the client must reload it.
        raise ApiError(
            ErrorCode.MAP_VERSION_MISMATCH,
            "The map changed. Start a new session.",
            {"required_map_version": map_.version if map_ else None},
        )
    return map_


def check_map_version(map_: MapData, client_version: str) -> None:
    if client_version != map_.version:
        raise ApiError(
            ErrorCode.MAP_VERSION_MISMATCH,
            "The map changed. Reload it and retry.",
            {"required_map_version": map_.version},
        )


def check_revision(row: SessionRow, expected: int) -> None:
    if expected != row.revision:
        raise ApiError(
            ErrorCode.REVISION_CONFLICT,
            "The session changed. Sync with navigation-state and retry.",
            {"revision": row.revision},
        )


def advance(row: SessionRow, trigger: Trigger, *, has_location: bool = False) -> None:
    """Apply a state-machine transition and bump the revision clients use for optimistic locking."""
    try:
        row.state = transition(State(row.state), trigger, has_location=has_location)
    except InvalidTransition as exc:
        raise ApiError(
            ErrorCode.INVALID_STATE, f"Not allowed while the session is {row.state}.", {"state": row.state}
        ) from exc
    row.revision += 1


def status_of(row: SessionRow) -> SessionStatus:
    return SessionStatus(state=row.state, revision=row.revision)


_JPEG_MAGIC = b"\xff\xd8\xff"


def read_jpeg(upload: UploadFile) -> bytes:
    """Photos must be JPEG within the size limit (API spec v0.3 §3). They are never written to disk or DB."""
    limit = get_settings().max_image_bytes
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise ApiError(ErrorCode.IMAGE_TOO_LARGE, f"Images must be {limit // (1024 * 1024)} MB or smaller.")
    if not data.startswith(_JPEG_MAGIC):
        raise ApiError(ErrorCode.UNSUPPORTED_IMAGE, "Images must be JPEG.")
    return data
