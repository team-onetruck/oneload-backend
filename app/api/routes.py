from fastapi import APIRouter

from app.api.deps import (
    CurrentSession,
    Db,
    Maps,
    advance,
    check_map_version,
    check_revision,
    map_of,
    replan,
    route_options,
    status_of,
    store_plan,
)
from app.domain.planning import StepStatus, route_plan
from app.domain.session_state import Trigger
from app.errors import ApiError, ErrorCode
from app.models import RouteRow
from app.openapi import Tag
from app.schemas import Preferences, RouteIn, RouteOut, RouteStepOut, Waypoint
from app.security import new_id

router = APIRouter(tags=[Tag.ROUTES])


@router.post(
    "/sessions/{session_id}/routes",
    summary="경로 계산",
    description=(
        "확정된 현재 위치에서 세션 목적지까지, 행동 계획(`plan`)의 단계를 차례로 거치는 경로를"
        " 등록된 통로만으로 만듭니다. 같은 일을 할 수 있는 장소가 여럿이면(예: 보증금 환급기) 각 구간이 아니라"
        " 전체 이동 거리가 가장 짧은 곳을 고르고, 지나는 개찰구마다 `PASS_GATE` 단계를 넣습니다."
        " 추천 단계는 사용자가 수락한 경우에만 경로에 들어갑니다. 각 구간에는 거리뷰 사진(`pano_id`),"
        " 화살표 방향(`arrow_yaw_deg`), 해당 행동 단계(`plan_step_id`)가 붙습니다. 목적지는 `/messages`에서"
        " 정해진 것과 같아야 하고, 위치가 바뀌었거나 길을 잃은 뒤에는 `reroute: true`로 다시 부릅니다."
    ),
)
def create_route(body: RouteIn, row: CurrentSession, db: Db, maps: Maps) -> RouteOut:
    # Only the backend plans routes; the agent can set a destination but never calls the route engine (spec v0.3 §10).
    check_revision(row, body.expected_revision)
    map_ = map_of(row, maps)
    check_map_version(map_, body.map_version)

    if map_.verified_poi(body.destination_poi_id) is None:
        raise ApiError(ErrorCode.POI_NOT_FOUND, f"Unknown destination {body.destination_poi_id}.")
    if body.destination_poi_id != row.destination_poi_id:
        raise ApiError(
            ErrorCode.INVALID_INPUT,
            "Set the destination through /messages first.",
            {"destination_poi_id": row.destination_poi_id},
        )
    if row.confirmed_node_id is None:
        raise ApiError(ErrorCode.LOCATION_NOT_CONFIRMED, "Confirm the current location first.")

    preferences = body.preferences or Preferences(**row.preferences)
    plan = replan(row, map_)
    routed = route_plan(map_, plan, row.confirmed_node_id, route_options(preferences), new_id)
    if routed is None:
        raise ApiError(
            ErrorCode.ROUTE_NOT_FOUND,
            "No registered route from here through the plan's steps.",
            {
                "start_node_id": row.confirmed_node_id,
                "avoid_stairs": preferences.avoid_stairs,
                "wheelchair": preferences.wheelchair,
                "blocked_step_ids": [s.step_id for s in plan.steps if s.status == StepStatus.BLOCKED],
            },
        )
    store_plan(row, plan)
    route = routed.route

    advance(row, Trigger.ROUTE_CREATED)

    # A reroute keeps the route id and bumps its revision; a new destination gets a new route.
    previous = db.get(RouteRow, row.active_route_id) if body.reroute and row.active_route_id else None
    route_id = previous.id if previous else new_id("route")
    route_revision = previous.revision + 1 if previous else 1
    out = RouteOut(
        route_id=route_id,
        route_revision=route_revision,
        map_id=map_.map_id,
        map_version=map_.version,
        start_node_id=row.confirmed_node_id,
        destination_poi_id=body.destination_poi_id,
        distance_m=route.distance_m,
        path_node_ids=route.path_node_ids,
        waypoints=[
            Waypoint(
                waypoint_id=f"w{i}", floor_id=map_.node_by_id[n].floor_id, position_map_m=map_.node_by_id[n].position
            )
            for i, n in enumerate(route.path_node_ids, start=1)
        ],
        steps=[
            RouteStepOut(
                step_id=s.step_id,
                kind=s.kind,
                from_node_id=s.from_node_id,
                to_node_id=s.to_node_id,
                distance_m=s.distance_m,
                instruction_en=s.instruction.en,
                instruction_ko=s.instruction.ko,
                pano_id=s.pano_id,
                arrow_yaw_deg=s.arrow_yaw_deg,
                plan_step_id=plan_step_id,
            )
            for s, plan_step_id in zip(route.steps, routed.plan_step_ids, strict=True)
        ],
        plan=plan,
        session=status_of(row),
    )

    body_json = out.model_dump(mode="json", exclude={"session"})
    if previous:
        previous.revision = route_revision
        previous.body = body_json
    else:
        db.add(RouteRow(id=route_id, session_id=row.id, revision=route_revision, body=body_json))
    row.active_route_id = route_id
    return out
