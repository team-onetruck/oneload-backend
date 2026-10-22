from fastapi import APIRouter

from app.api.deps import CurrentSession, Db, Maps, advance, check_map_version, check_revision, map_of, status_of
from app.domain.routing import plan_route
from app.domain.session_state import Trigger
from app.errors import ApiError, ErrorCode
from app.models import RouteRow
from app.schemas import Preferences, RouteIn, RouteOut, RouteStepOut, Waypoint
from app.security import new_id

router = APIRouter(tags=["routes"])


@router.post("/sessions/{session_id}/routes")
def create_route(body: RouteIn, row: CurrentSession, db: Db, maps: Maps) -> RouteOut:
    """Route from the confirmed location to the session's destination. Only the backend plans routes;
    the agent can set a destination but never calls the route engine (API spec v0.3 §10)."""
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
    goal = map_.poi_by_id[body.destination_poi_id].node_id
    planned = plan_route(map_, row.confirmed_node_id, goal, avoid_stairs=preferences.avoid_stairs)
    if planned is None:
        raise ApiError(
            ErrorCode.ROUTE_NOT_FOUND,
            "No registered route from here.",
            {"start_node_id": row.confirmed_node_id, "avoid_stairs": preferences.avoid_stairs},
        )

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
        distance_m=planned.distance_m,
        path_node_ids=planned.path_node_ids,
        waypoints=[
            Waypoint(
                waypoint_id=f"w{i}", floor_id=map_.node_by_id[n].floor_id, position_map_m=map_.node_by_id[n].position
            )
            for i, n in enumerate(planned.path_node_ids, start=1)
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
            )
            for s in planned.steps
        ],
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
