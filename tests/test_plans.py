"""The action plan through the HTTP API, on the invented mock station (see test_planning.py)."""

from typing import Any

from fastapi.testclient import TestClient

from app.ai.contracts import AgentResponse, AgentResult, Destination, Slots
from app.domain.mapdata import MapStore
from app.main import create_app
from tests.conftest import Api, FakeVision, error_code


def _patch(api: Api, **body: Any) -> dict[str, Any]:
    response = api.http.patch(api.url(""), json={"expected_revision": api.revision, **body}, headers=api.headers())
    assert response.status_code == 200, response.text
    return api._remember(response.json())


def _act(api: Api, step_id: str, action: str) -> dict[str, Any]:
    return api.post(f"/steps/{step_id}/{action}", {"expected_revision": api.revision})


def _route(api: Api, destination: str, **extra: Any) -> dict[str, Any]:
    body = {"expected_revision": api.revision, "destination_poi_id": destination, "map_version": api.map_version}
    return api.post("/routes", {**body, **extra})


def _stand_at(api: Api, node_id: str) -> None:
    api.post(
        "/location-confirmations", {"expected_revision": api.revision, "method": "manual", "selected_node_id": node_id}
    )


def test_leaving_with_a_single_use_card_refunds_on_the_way_out(mock_api: Api):
    mock_api.start("ko")
    _patch(mock_api, ticket_type="SINGLE_USE", refund_status="NOT_REFUNDED")
    offered = mock_api.post("/messages", {"selected_poi_id": "poi-exit3"})["plan"]["steps"][0]
    assert (offered["type"], offered["priority"], offered["accepted"]) == (
        "REFUND_SINGLE_USE_CARD",
        "RECOMMENDED",
        False,
    )

    _stand_at(mock_api, "paid-hall")
    accepted = _act(mock_api, offered["step_id"], "accept")
    route = _route(mock_api, "poi-exit3")
    gate, refund, exit_ = route["plan"]["steps"]

    assert accepted["plan"]["steps"][0]["accepted"] is True
    assert [(s["type"], s["status"]) for s in (gate, refund, exit_)] == [
        ("PASS_GATE", "ACTIVE"),
        ("REFUND_SINGLE_USE_CARD", "PENDING"),
        ("MOVE_TO_DESTINATION", "PENDING"),
    ]
    assert refund["target_poi_id"] == "poi-refund-way"
    assert [s["plan_step_id"] for s in route["steps"]] == [gate["step_id"], refund["step_id"], exit_["step_id"]]

    through_gate = _act(mock_api, gate["step_id"], "complete")
    _act(mock_api, refund["step_id"], "complete")
    rest = _route(mock_api, "poi-exit3", reroute=True)

    assert through_gate["facts"] == {
        "ticket_type": "SINGLE_USE",
        "refund_status": "NOT_REFUNDED",
        "area_state": "FREE",
        "location_method": "step_completed",
    }
    assert through_gate["plan"]["current_step_id"] == refund["step_id"]
    # The new route starts where the user reported the last finished step, with nothing left but the exit.
    assert (rest["start_node_id"], rest["path_node_ids"]) == ("refund-way", ["refund-way", "exit3"])
    assert rest["plan"]["current_step_id"] == exit_["step_id"]
    assert mock_api.http.get(mock_api.url("/plan"), headers=mock_api.headers()).json()["facts"]["refund_status"] == (
        "REFUNDED"
    )


def test_a_missing_ticket_type_is_reported_then_planned(mock_api: Api):
    mock_api.start()
    first = mock_api.post("/messages", {"selected_poi_id": "poi-express-platform"})["plan"]

    patched = _patch(mock_api, ticket_type="TRANSIT_CARD")["plan"]

    assert (first["missing_facts"], [s["type"] for s in first["steps"]]) == (["ticket_type"], ["MOVE_TO_DESTINATION"])
    assert patched["missing_facts"] == []
    assert [(s["type"], s["priority"], s["candidate_poi_ids"]) for s in patched["steps"]] == [
        ("BUY_TICKET", "REQUIRED", ["poi-express-tickets"]),
        ("MOVE_TO_DESTINATION", "REQUIRED", ["poi-express-platform"]),
    ]
    assert patched["plan_id"] == first["plan_id"]


def test_step_actions_are_checked(mock_api: Api):
    mock_api.start()
    required = mock_api.post("/messages", {"selected_poi_id": "poi-exit5"})["plan"]["steps"][0]

    skipped = mock_api.raw_post(f"/steps/{required['step_id']}/skip", {"expected_revision": mock_api.revision})
    unknown = mock_api.raw_post("/steps/step_nope/complete", {"expected_revision": mock_api.revision})

    assert (skipped.status_code, error_code(skipped)) == (409, "INVALID_STATE")
    assert (unknown.status_code, error_code(unknown)) == (404, "NOT_FOUND")


def test_unreachable_destination_reports_route_not_found(mock_api: Api):
    mock_api.start()
    _patch(mock_api, preferences={"wheelchair": True})
    mock_api.post("/messages", {"selected_poi_id": "poi-exit5"})  # stairs only
    _stand_at(mock_api, "free-hall")

    body = {"expected_revision": mock_api.revision, "destination_poi_id": "poi-exit5", "map_version": "test-1"}
    response = mock_api.raw_post("/routes", body)

    assert (response.status_code, error_code(response)) == (422, "ROUTE_NOT_FOUND")
    assert response.json()["error"]["details"]["wheelchair"] is True


def test_a_plan_made_on_an_older_map_is_rejected(mock_api: Api, mock_station):
    mock_api.start()
    mock_api.post("/messages", {"selected_poi_id": "poi-exit3"})
    surveyed_again = mock_station.model_copy(update={"version": "test-2"})

    with TestClient(create_app(map_store=MapStore([surveyed_again]), vision=FakeVision())) as http:
        response = http.get(mock_api.url("/plan"), headers=mock_api.headers())

    assert (response.status_code, error_code(response)) == (409, "MAP_VERSION_MISMATCH")


def test_the_agents_card_answer_fills_in_the_ticket_type(api: Api):
    api.start()
    api.say("All-stop")
    api.say("I don't have one")

    facts = api.http.get(api.url("/plan"), headers=api.headers()).json()["facts"]

    assert facts["ticket_type"] == "NONE"


class _SaysSingleUseNotRefunded:
    """An agent that heard "I have a single-use card and haven't refunded it, take me to exit 3"."""

    def handle_message(self, ctx, message, image, selected_poi_id) -> AgentResult:
        response = AgentResponse(
            status="destination_set",
            intent="navigate_to_exit",
            assistant_message="3번 출구로 안내할게요.",
            slots=Slots(has_single_use_card="yes", single_use_card_returned="no"),
            destination=Destination(poi_id="poi-exit3"),
            next_action="locate",
        )
        return AgentResult(response, frozenset())


def test_ticket_facts_told_to_the_agent_reach_the_plan_without_overriding_the_client(mock_station):
    store = MapStore([mock_station])
    with TestClient(create_app(map_store=store, agent=_SaysSingleUseNotRefunded(), vision=FakeVision())) as http:
        heard = Api(http=http, vision=FakeVision(), map_id=mock_station.map_id)
        heard.start()
        told_by_agent = heard.say("1회용 카드 있고 환급 안 했어요. 3번 출구")

        client_set = Api(http=http, vision=FakeVision(), map_id=mock_station.map_id)
        client_set.start()
        _patch(client_set, ticket_type="TRANSIT_CARD")
        kept = client_set.say("1회용 카드 있고 환급 안 했어요. 3번 출구")

    assert [s["type"] for s in told_by_agent["plan"]["steps"]] == ["REFUND_SINGLE_USE_CARD", "MOVE_TO_DESTINATION"]
    assert [s["type"] for s in kept["plan"]["steps"]] == ["MOVE_TO_DESTINATION"]
