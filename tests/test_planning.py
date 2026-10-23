"""Task planner, rule engine and multi-stop routing on the invented mock station (tests/fixtures).

Layout: paid-hall -gate-> free-hall. From free-hall: a closed refund machine (1 m), a refund machine
away from the exit (10 m), one on the way to exit 3 (30 m), exit 3 by stairs or by a lift detour,
exit 5 by stairs only, and the express ticket office (10 m). The express platform is below paid-hall.
"""

import itertools

import pytest

from app.domain.mapdata import MapData
from app.domain.planning import (
    AreaState,
    Facts,
    Plan,
    Priority,
    RefundStatus,
    StepNotAllowed,
    StepStatus,
    StepType,
    TicketType,
    accept_step,
    build_plan,
    complete_step,
    goal_for,
    route_plan,
    skip_step,
)
from app.domain.routing import RouteOptions

ANY = RouteOptions()


_counter = itertools.count(1)


def new_id(prefix: str) -> str:
    return f"{prefix}-{next(_counter)}"


def _plan(station: MapData, poi_id: str, **facts) -> Plan:
    destination = station.poi_by_id[poi_id]
    return build_plan(station, goal_for(destination), destination, Facts(**facts), new_id)


def _types(plan: Plan) -> list[tuple[str, str]]:
    return [(s.type, s.status) for s in plan.steps]


def test_leaving_by_an_exit_passes_the_gate_first(mock_station):
    plan = _plan(mock_station, "poi-exit3", ticket_type=TicketType.TRANSIT_CARD)

    routed = route_plan(mock_station, plan, "paid-hall", ANY, new_id)

    assert routed.route.path_node_ids == ["paid-hall", "free-hall", "refund-way", "exit3"]
    assert _types(plan) == [
        (StepType.PASS_GATE, StepStatus.ACTIVE),
        (StepType.MOVE_TO_DESTINATION, StepStatus.PENDING),
    ]
    gate, exit_ = plan.steps
    assert routed.plan_step_ids == [gate.step_id, exit_.step_id, exit_.step_id]


def test_single_use_card_is_offered_a_refund_that_only_joins_the_route_once_accepted(mock_station):
    plan = _plan(mock_station, "poi-exit3", ticket_type=TicketType.SINGLE_USE, refund_status=RefundStatus.NOT_REFUNDED)
    refund = plan.steps[0]
    assert (refund.type, refund.priority, refund.accepted) == (
        StepType.REFUND_SINGLE_USE_CARD,
        Priority.RECOMMENDED,
        False,
    )
    assert "poi-refund-closed" not in refund.candidate_poi_ids  # out of service

    route_plan(mock_station, plan, "free-hall", ANY, new_id)
    assert refund.target_poi_id is None  # offered, not routed

    accept_step(plan, refund.step_id)
    routed = route_plan(mock_station, plan, "free-hall", ANY, new_id)

    # The nearest open machine (10 m, away from the exit) would make the whole walk 20 m longer.
    assert refund.target_poi_id == "poi-refund-way"
    assert routed.route.distance_m == 55


@pytest.mark.parametrize(
    ("ticket_type", "refund_status"),
    [
        (TicketType.TRANSIT_CARD, RefundStatus.UNKNOWN),  # a normal card has no deposit
        (TicketType.SINGLE_USE, RefundStatus.REFUNDED),  # already got the deposit back
    ],
)
def test_no_refund_step_when_there_is_nothing_to_refund(mock_station, ticket_type, refund_status):
    plan = _plan(mock_station, "poi-exit3", ticket_type=ticket_type, refund_status=refund_status)

    assert [s.type for s in plan.steps] == [StepType.MOVE_TO_DESTINATION]
    assert plan.missing_facts == []


def test_unknown_facts_are_asked_for_not_guessed(mock_station):
    exit_plan = _plan(mock_station, "poi-exit3", ticket_type=TicketType.SINGLE_USE)
    train_plan = _plan(mock_station, "poi-express-platform")

    assert exit_plan.missing_facts == ["refund_status"]
    assert train_plan.missing_facts == ["ticket_type"]
    assert [s.type for s in exit_plan.steps + train_plan.steps] == [StepType.MOVE_TO_DESTINATION] * 2


def test_express_train_needs_a_ticket_bought_before_the_gate(mock_station):
    with_card = _plan(mock_station, "poi-express-platform", ticket_type=TicketType.TRANSIT_CARD)
    with_ticket = _plan(mock_station, "poi-express-platform", ticket_type=TicketType.EXPRESS_TICKET)

    route_plan(mock_station, with_card, "free-hall", ANY, new_id)

    assert [(s.type, s.target_poi_id) for s in with_card.steps] == [
        (StepType.BUY_TICKET, "poi-express-tickets"),
        (StepType.PASS_GATE, None),
        (StepType.MOVE_TO_DESTINATION, "poi-express-platform"),
    ]
    assert [s.type for s in with_ticket.steps] == [StepType.MOVE_TO_DESTINATION]


def test_avoid_stairs_takes_the_lift_and_wheelchair_cannot_reach_a_stairs_only_exit(mock_station):
    to_exit3 = _plan(mock_station, "poi-exit3", ticket_type=TicketType.TRANSIT_CARD)
    to_exit5 = _plan(mock_station, "poi-exit5", ticket_type=TicketType.TRANSIT_CARD)

    step_free = route_plan(mock_station, to_exit3, "free-hall", RouteOptions(avoid_stairs=True), new_id)

    assert step_free.route.path_node_ids == ["free-hall", "refund-way", "lift", "exit3"]
    assert route_plan(mock_station, to_exit5, "free-hall", RouteOptions(wheelchair=True), new_id) is None


def test_a_step_with_no_open_place_is_blocked_and_the_trip_goes_on(mock_station):
    all_closed = mock_station.model_copy(
        update={"pois": [p.model_copy(update={"closed": p.category == "card_refund"}) for p in mock_station.pois]}
    )
    plan = _plan(all_closed, "poi-exit3", ticket_type=TicketType.SINGLE_USE, refund_status=RefundStatus.NOT_REFUNDED)

    routed = route_plan(all_closed, plan, "free-hall", ANY, new_id)

    assert _types(plan) == [
        (StepType.REFUND_SINGLE_USE_CARD, StepStatus.BLOCKED),
        (StepType.MOVE_TO_DESTINATION, StepStatus.ACTIVE),
    ]
    assert routed.route.path_node_ids[-1] == "exit3"


def test_finishing_steps_moves_on_and_updates_what_we_know(mock_station):
    facts = Facts(ticket_type=TicketType.SINGLE_USE, refund_status=RefundStatus.NOT_REFUNDED, area_state=AreaState.PAID)
    plan = _plan(mock_station, "poi-exit3", **facts.model_dump())
    accept_step(plan, plan.steps[0].step_id)
    route_plan(mock_station, plan, "paid-hall", ANY, new_id)
    gate, refund, exit_ = plan.steps

    with pytest.raises(StepNotAllowed):
        complete_step(mock_station, plan, refund.step_id, facts)  # not the current step yet
    with pytest.raises(StepNotAllowed):
        skip_step(plan, gate.step_id)  # required steps cannot be skipped

    through_gate = complete_step(mock_station, plan, gate.step_id, facts)
    refunded = complete_step(mock_station, plan, refund.step_id, through_gate.facts)

    assert (through_gate.node_id, through_gate.facts.area_state) == ("free-hall", AreaState.FREE)
    assert (refunded.node_id, refunded.facts.refund_status) == ("refund-way", RefundStatus.REFUNDED)
    assert plan.current_step_id == exit_.step_id


def test_replanning_the_same_trip_keeps_finished_steps(mock_station):
    plan = _plan(mock_station, "poi-express-platform", ticket_type=TicketType.TRANSIT_CARD)
    route_plan(mock_station, plan, "free-hall", ANY, new_id)
    bought = complete_step(mock_station, plan, plan.steps[0].step_id, Facts(ticket_type=TicketType.TRANSIT_CARD))
    destination = mock_station.poi_by_id["poi-express-platform"]

    again = build_plan(mock_station, plan.goal, destination, bought.facts, new_id, previous=plan)

    assert bought.facts.ticket_type == TicketType.EXPRESS_TICKET
    assert again.plan_id == plan.plan_id
    assert _types(again) == [
        (StepType.BUY_TICKET, StepStatus.COMPLETED),
        (StepType.MOVE_TO_DESTINATION, StepStatus.ACTIVE),
    ]
