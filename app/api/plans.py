"""The action plan: see the steps, accept or skip recommended ones, report a step done."""

from collections.abc import Callable

from fastapi import APIRouter

from app.api.deps import (
    CurrentSession,
    Maps,
    check_revision,
    facts_of,
    map_of,
    plan_of,
    status_of,
    store_facts,
    store_plan,
)
from app.domain.mapdata import MapData
from app.domain.planning import Plan, StepNotAllowed, UnknownStep, accept_step, complete_step, skip_step
from app.errors import ApiError, ErrorCode
from app.models import SessionRow
from app.openapi import Tag
from app.schemas import PlanStateOut, StepActionIn, TravelFacts

router = APIRouter(tags=[Tag.PLAN])


@router.get(
    "/sessions/{session_id}/plan",
    summary="행동 계획 조회",
    description=(
        "현재 행동 계획과 진행 상태, 계획에 쓰인 사용자 정보(승차권, 보증금 환급, 유료구역 여부)를 돌려줍니다."
        " 지금 할 일은 `plan.current_step_id`입니다. 추천 단계(`RECOMMENDED`)는 수락 전까지 경로에 들어가지 않습니다."
    ),
)
def get_plan(row: CurrentSession, maps: Maps) -> PlanStateOut:
    map_of(row, maps)  # a plan made on an older map is no longer valid: 409 MAP_VERSION_MISMATCH
    return _state(row, plan_of(row), requires_route_refresh=False)


@router.post(
    "/sessions/{session_id}/steps/{step_id}/accept",
    summary="추천 단계 수락",
    description="추천·선택 단계(예: 보증금 환급)를 경로에 넣습니다. 경로를 `reroute: true`로 다시 받아야 합니다.",
)
def accept(step_id: str, body: StepActionIn, row: CurrentSession, maps: Maps) -> PlanStateOut:
    return _change(row, maps, body, step_id, lambda _map, plan: accept_step(plan, step_id), reroute=True)


@router.post(
    "/sessions/{session_id}/steps/{step_id}/skip",
    summary="추천 단계 건너뛰기",
    description="추천·선택 단계를 하지 않기로 합니다. 필수 단계(`REQUIRED`)는 건너뛸 수 없습니다(409 INVALID_STATE).",
)
def skip(step_id: str, body: StepActionIn, row: CurrentSession, maps: Maps) -> PlanStateOut:
    return _change(row, maps, body, step_id, lambda _map, plan: skip_step(plan, step_id), reroute=True)


@router.post(
    "/sessions/{session_id}/steps/{step_id}/complete",
    summary="단계 완료 보고",
    description=(
        "사용자가 지금 단계(`current_step_id`)를 마쳤다고 알립니다. 다음 단계가 ACTIVE가 되고,"
        " 마친 일에 따라 사용자 정보가 바뀝니다: 표를 사면 `ticket_type`, 환급하면 `refund_status`,"
        " 개찰구를 지나면 `area_state`. 현재 위치는 그 단계의 장소로 옮겨집니다(`location_method: step_completed`)."
    ),
)
def complete(step_id: str, body: StepActionIn, row: CurrentSession, maps: Maps) -> PlanStateOut:
    def finish(map_: MapData, plan: Plan) -> None:
        done = complete_step(map_, plan, step_id, facts_of(row))
        store_facts(row, done.facts)
        if done.node_id:
            row.confirmed_node_id = done.node_id
            row.confirmed_heading_deg = None
            row.location_method = "step_completed"

    # The rest of the current route still leads on from here, so no reroute is needed.
    return _change(row, maps, body, step_id, finish, reroute=False)


def _change(
    row: SessionRow,
    maps: Maps,
    body: StepActionIn,
    step_id: str,
    change: Callable[[MapData, Plan], None],
    *,
    reroute: bool,
) -> PlanStateOut:
    check_revision(row, body.expected_revision)
    map_ = map_of(row, maps)
    plan = plan_of(row)
    if plan is None:
        raise ApiError(ErrorCode.INVALID_STATE, "No plan yet. Set a destination through /messages first.")
    try:
        change(map_, plan)
    except UnknownStep as exc:
        raise ApiError(ErrorCode.NOT_FOUND, f"Unknown step {step_id}.") from exc
    except StepNotAllowed as exc:
        raise ApiError(ErrorCode.INVALID_STATE, str(exc), {"step_id": step_id}) from exc
    store_plan(row, plan)
    row.revision += 1
    return _state(row, plan, requires_route_refresh=reroute and row.active_route_id is not None)


def _state(row: SessionRow, plan: Plan | None, *, requires_route_refresh: bool) -> PlanStateOut:
    return PlanStateOut(
        plan=plan,
        facts=TravelFacts(**facts_of(row).model_dump(), location_method=row.location_method),
        requires_route_refresh=requires_route_refresh,
        session=status_of(row),
    )
