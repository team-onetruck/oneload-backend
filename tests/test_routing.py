from app.domain.mapdata import MapData
from app.domain.routing import RouteOptions, plan_route


def test_route_follows_registered_passages_with_street_view_arrows(seoul):
    route = plan_route(seoul, "line4-gates", "express-tickets")

    assert route.path_node_ids == ["line4-gates", "arex-concourse", "express-tickets"]
    assert [s.kind for s in route.steps] == ["escalator_down", "walk"]
    assert route.distance_m == 140
    # Arrow = photo north (-320°) + leg heading (240°) = -80°, and 162° + 90° wraps to -108°.
    assert [(s.pano_id, s.arrow_yaw_deg) for s in route.steps] == [
        ("pano-line4-gates", -80),
        ("pano-arex-concourse", -108),
    ]


def test_step_without_panorama_has_no_arrow(seoul):
    route = plan_route(seoul, "express-tickets", "express-platform")

    assert route.steps[0].from_node_id == "express-tickets"
    assert (route.steps[0].pano_id, route.steps[0].arrow_yaw_deg) == (None, None)


def test_no_route_when_no_passage_is_registered(seoul):
    # Express and all-stop areas are only connected through the concourse, in one direction.
    assert plan_route(seoul, "express-tickets", "allstop-tickets") is None


def _node(node_id: str, y: float) -> dict:
    return {
        "id": node_id,
        "floor_id": "B1",
        "position": {"x": 0, "y": y, "z": 0},
        "name": {"en": node_id, "ko": node_id},
    }


def _edge(start: str, end: str, edge_type: str, meters: float) -> dict:
    return {
        "from": start,
        "to": end,
        "type": edge_type,
        "distance_m": meters,
        "heading_deg": 0,
        "instruction": {"en": "", "ko": ""},
    }


def test_avoid_stairs_takes_the_longer_step_free_route():
    # a --stairs 10 m--> b   versus   a --corridor 5 m--> lift --elevator 30 m--> b
    map_ = MapData.model_validate(
        {
            "map_id": "t",
            "version": "1",
            "name": {"en": "t", "ko": "t"},
            "provenance": {},
            "coordinate_system": {},
            "floors": [{"floor_id": "B1", "elevation_m": 0}],
            "nodes": [_node("a", 0), _node("lift", 0), _node("b", -5)],
            "edges": [
                _edge("a", "b", "stairs", 10),
                _edge("a", "lift", "corridor", 5),
                _edge("lift", "b", "elevator", 30),
            ],
            "pois": [],
            "landmarks": [],
            "panoramas": [],
        }
    )

    assert plan_route(map_, "a", "b").path_node_ids == ["a", "b"]
    assert plan_route(map_, "a", "b", RouteOptions(avoid_stairs=True)).path_node_ids == ["a", "lift", "b"]
