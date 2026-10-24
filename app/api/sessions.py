from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import (
    CurrentSession,
    Db,
    Maps,
    advance,
    check_revision,
    facts_of,
    map_of,
    plan_of,
    replan,
    status_of,
)
from app.config import get_settings
from app.domain.session_state import State, Trigger
from app.errors import ApiError, ErrorCode
from app.models import FeedbackRow, NavigationEventRow, RouteRow, SessionRow
from app.openapi import Tag
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

router = APIRouter(tags=[Tag.SESSIONS])


@router.post(
    "/sessions",
    status_code=201,
    summary="세션 생성",
    description="앱을 열 때 한 번 호출합니다. 응답의 `session_token`을 이후 모든 세션 API에 Bearer 토큰으로 보냅니다."
    " 2시간 동안 요청이 없으면 만료됩니다.",
)
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


@router.patch(
    "/sessions/{session_id}",
    summary="언어·이동 선호·승차권 정보 변경",
    description=(
        "언어, 계단 회피·휠체어 같은 선호, 가진 승차권(`ticket_type`)과 보증금 환급 여부(`refund_status`)를"
        " 바꿉니다. 승차권 정보는 행동 계획의 규칙 조건이라 바뀌면 계획을 다시 세웁니다"
        " (`plan.missing_facts`에 있던 값을 여기로 보내세요). 목적지는 바꿀 수 없고 `/messages`로만 지정합니다."
    ),
)
def update_session(body: PatchSessionIn, row: CurrentSession, maps: Maps) -> PatchSessionOut:
    check_revision(row, body.expected_revision)
    map_ = map_of(row, maps)
    preferences_changed = body.preferences is not None and body.preferences.model_dump() != row.preferences
    if body.language:
        row.language = body.language
    if body.preferences:
        row.preferences = body.preferences.model_dump()

    before = facts_of(row)
    reported = {"ticket_type": body.ticket_type, "refund_status": body.refund_status}
    for fact, value in reported.items():
        if value:
            setattr(row, fact, value)
    # A new list, so SQLAlchemy sees the JSON column change.
    row.client_facts = sorted({*row.client_facts, *(fact for fact, value in reported.items() if value)})
    facts_changed = facts_of(row) != before
    plan = replan(row, map_) if facts_changed else plan_of(row)

    row.revision += 1
    return PatchSessionOut(
        session_id=row.id,
        language=row.language,
        preferences=Preferences(**row.preferences),
        facts=facts_of(row),
        plan=plan,
        requires_route_refresh=(preferences_changed or facts_changed) and row.active_route_id is not None,
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


@router.get(
    "/sessions/{session_id}/navigation-state",
    summary="현재 진행 상태 조회",
    description="앱 재시작이나 `REVISION_CONFLICT` 뒤에 상태를 다시 맞출 때 씁니다."
    " `next_action`이 다음에 할 일을 알려줍니다.",
)
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


@router.post(
    "/sessions/{session_id}/navigation-events",
    summary="길안내 이벤트 보고",
    description=(
        "`navigation_started`(안내 시작 → navigating), `arrived`(도착 → arrived, 도착 지점이 새 현재 위치),"
        " `navigation_cancelled`(취소 → idle)을 묶어서 보냅니다. 오프라인 동안 쌓인 이벤트를 한 번에 보내도 되고,"
        " 같은 `event_id`는 한 번만 반영됩니다."
    ),
)
def report_navigation_events(body: NavigationEventsIn, row: CurrentSession, db: Db, maps: Maps) -> NavigationEventsOut:
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


@router.post(
    "/sessions/{session_id}/feedback",
    status_code=201,
    summary="피드백 보내기",
    description="안내 만족도(1~5)와 오류 신고를 기록합니다. QA·평가(R6)에 쓰입니다.",
)
def send_feedback(body: FeedbackIn, row: CurrentSession, db: Db) -> FeedbackOut:
    feedback = FeedbackRow(id=new_id("fb"), session_id=row.id, **body.model_dump())
    db.add(feedback)
    return FeedbackOut(feedback_id=feedback.id)
