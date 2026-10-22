from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentSession, Db, Maps, advance, check_revision, map_of, status_of
from app.config import get_settings
from app.domain.session_state import State, Trigger
from app.errors import ApiError, ErrorCode
from app.models import FeedbackRow, NavigationEventRow, RouteRow, SessionRow
from app.schemas import (
    CreateSessionIn,
    CreateSessionOut,
    FeedbackIn,
    FeedbackOut,
    NavigationEventsIn,
    NavigationEventsOut,
    NavigationStateOut,
    PatchSessionIn,
    PatchSessionOut,
    Preferences,
    StateNextAction,
)
from app.security import new_id, new_session_token, sha256_hex

router = APIRouter(tags=["sessions"])


@router.post("/sessions", status_code=201)
def create_session(body: CreateSessionIn, db: Db, maps: Maps) -> CreateSessionOut:
    map_ = maps.get(body.map_id)
    if map_ is None:
        raise ApiError(ErrorCode.MAP_NOT_FOUND, f"Unknown map {body.map_id}.")

    token = new_session_token()
    row = SessionRow(
        id=new_id("ses"),
        token_hash=sha256_hex(token),
        language=body.language,
        map_id=map_.map_id,
        map_version=map_.version,
        client=body.client.model_dump(),
        state=State.IDLE,
        revision=1,
        slots={},
        preferences=Preferences().model_dump(),
    )
    db.add(row)
    return CreateSessionOut(
        session_id=row.id,
        session_token=token,
        expires_in_s=get_settings().session_idle_seconds,
        language=body.language,
        map_id=map_.map_id,
        map_version=map_.version,
        session=status_of(row),
    )


@router.patch("/sessions/{session_id}")
def update_session(body: PatchSessionIn, row: CurrentSession) -> PatchSessionOut:
    check_revision(row, body.expected_revision)
    preferences_changed = body.preferences is not None and body.preferences.model_dump() != row.preferences
    if body.language:
        row.language = body.language
    if body.preferences:
        row.preferences = body.preferences.model_dump()
    row.revision += 1
    return PatchSessionOut(
        session_id=row.id,
        language=row.language,
        preferences=Preferences(**row.preferences),
        requires_route_refresh=preferences_changed and row.active_route_id is not None,
        session=status_of(row),
    )


_NEXT_ACTION: dict[State, StateNextAction] = {
    State.IDLE: "reply",
    State.NEEDS_CLARIFICATION: "reply",
    State.NEEDS_LOCATION: "locate",
    State.NEEDS_CONFIRMATION: "confirm_location",
    State.READY_TO_NAVIGATE: "navigate",
    State.NAVIGATING: "continue_navigation",
    State.ARRIVED: "none",
    State.FAILED: "none",
}


@router.get("/sessions/{session_id}/navigation-state")
def navigation_state(row: CurrentSession, db: Db) -> NavigationStateOut:
    route = db.get(RouteRow, row.active_route_id) if row.active_route_id else None
    return NavigationStateOut(
        session=status_of(row),
        destination_poi_id=row.destination_poi_id,
        confirmed_node_id=row.confirmed_node_id,
        active_route_id=row.active_route_id,
        route_revision=route.revision if route else None,
        map_version=row.map_version,
        next_action=_NEXT_ACTION[State(row.state)],
    )


@router.post("/sessions/{session_id}/navigation-events")
def report_navigation_events(body: NavigationEventsIn, row: CurrentSession, db: Db, maps: Maps) -> NavigationEventsOut:
    """Events arrive in batches because the client queues them while offline; event_id makes resends harmless."""
    if body.route_id and body.route_id != row.active_route_id:
        raise ApiError(
            ErrorCode.INVALID_INPUT, "route_id is not the active route.", {"active_route_id": row.active_route_id}
        )

    ids = [e.event_id for e in body.events]
    seen = set(
        db.scalars(
            select(NavigationEventRow.event_id).where(
                NavigationEventRow.session_id == row.id, NavigationEventRow.event_id.in_(ids)
            )
        )
    )

    for event in body.events:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        if event.type == "navigation_started":
            if not row.active_route_id:
                raise ApiError(
                    ErrorCode.INVALID_STATE, "Create a route before starting navigation.", {"state": row.state}
                )
            advance(row, Trigger.NAVIGATION_STARTED)
        elif event.type == "arrived":
            advance(row, Trigger.ARRIVED)
            # Arriving confirms the user's position, so the next destination can start from here.
            row.confirmed_node_id = map_of(row, maps).poi_by_id[row.destination_poi_id].node_id
            row.active_route_id = None
        elif event.type == "navigation_cancelled":
            advance(row, Trigger.NAVIGATION_CANCELLED)
            row.destination_poi_id = None
            row.active_route_id = None
        db.add(
            NavigationEventRow(
                session_id=row.id,
                event_id=event.event_id,
                route_id=body.route_id,
                type=event.type,
                client_timestamp=event.client_timestamp,
                context=event.context,
            )
        )

    return NavigationEventsOut(accepted_event_ids=ids, session=status_of(row))


@router.post("/sessions/{session_id}/feedback", status_code=201)
def send_feedback(body: FeedbackIn, row: CurrentSession, db: Db) -> FeedbackOut:
    feedback = FeedbackRow(id=new_id("fb"), session_id=row.id, **body.model_dump())
    db.add(feedback)
    return FeedbackOut(feedback_id=feedback.id)
