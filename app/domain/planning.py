"""Task planner and rule engine: what the user has to do on the way, not only where to walk.

A plan is an ordered list of steps (buy a ticket, pass the gates, refund a card, reach the
destination). Steps come from RULES, each tied to a published source. When a rule depends on a
fact the session does not know yet (ticket type, refund status), nothing is guessed: the fact is
listed in `missing_facts` so the client can ask. Recommended steps are offered, never imposed:
they join the route only after the user accepts them.

Enum values follow the agent contract (upper case), so the same words travel to the agent later.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, computed_field

from app.domain.mapdata import MapData, Poi, TrainType
from app.domain.routing import PlannedRoute, RouteOptions, plan_route_via

# Makes a new opaque id with the given prefix (app.security.new_id); injected to keep this module pure.
NewId = Callable[[str], str]


class Goal(StrEnum):
    EXIT_STATION = "EXIT_STATION"
    FIND_FACILITY = "FIND_FACILITY"
    BOARD_TRAIN = "BOARD_TRAIN"
    BUY_TICKET = "BUY_TICKET"


class StepType(StrEnum):
    BUY_TICKET = "BUY_TICKET"
    PASS_GATE = "PASS_GATE"
    REFUND_SINGLE_USE_CARD = "REFUND_SINGLE_USE_CARD"
    MOVE_TO_DESTINATION = "MOVE_TO_DESTINATION"


class Priority(StrEnum):
    REQUIRED = "REQUIRED"
    RECOMMENDED = "RECOMMENDED"
    OPTIONAL = "OPTIONAL"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"


class TicketType(StrEnum):
    UNKNOWN = "UNKNOWN"
    TRANSIT_CARD = "TRANSIT_CARD"
    SINGLE_USE = "SINGLE_USE"
    EXPRESS_TICKET = "EXPRESS_TICKET"
    NONE = "NONE"


class RefundStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    NOT_REFUNDED = "NOT_REFUNDED"
    REFUNDED = "REFUNDED"


class AreaState(StrEnum):
    UNKNOWN = "UNKNOWN"
    PAID = "PAID"
    FREE = "FREE"


class Facts(BaseModel):
    ticket_type: TicketType = TicketType.UNKNOWN
    refund_status: RefundStatus = RefundStatus.UNKNOWN
    area_state: AreaState = AreaState.UNKNOWN


UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    goal: Goal
    # Fact name -> the values that trigger the rule. Facts: ticket_type, refund_status, train_type.
    when: dict[str, frozenset[str]]
    step: StepType
    priority: Priority
    target_category: str
    target_train_type: TrainType | None
    source: str


_ANY_TICKET_BUT_EXPRESS = frozenset({TicketType.TRANSIT_CARD, TicketType.SINGLE_USE, TicketType.NONE})

RULES: tuple[Rule, ...] = (
    Rule(
        rule_id="BUY_EXPRESS_TICKET",
        goal=Goal.BOARD_TRAIN,
        when={"train_type": frozenset({"express"}), "ticket_type": _ANY_TICKET_BUT_EXPRESS},
        step=StepType.BUY_TICKET,
        priority=Priority.REQUIRED,
        target_category="tickets",
        target_train_type="express",
        # "직통열차는 … 탑승 전 승차권을 별도로 구매하셔야 합니다." Seoul Station: B2 express service center.
        source="https://www.airportrailroad.com/train/express/introduce",
    ),
    Rule(
        rule_id="BUY_SINGLE_USE_CARD",
        goal=Goal.BOARD_TRAIN,
        when={"train_type": frozenset({"allstop"}), "ticket_type": frozenset({TicketType.NONE})},
        step=StepType.BUY_TICKET,
        priority=Priority.REQUIRED,
        target_category="tickets",
        target_train_type="allstop",
        # "1회용 교통카드 : 각 역 승차권 자동발매기"
        source="https://www.airportrailroad.com/train/normal/introduce",
    ),
    Rule(
        rule_id="REFUND_SINGLE_USE_CARD",
        goal=Goal.EXIT_STATION,
        when={
            "ticket_type": frozenset({TicketType.SINGLE_USE}),
            "refund_status": frozenset({RefundStatus.NOT_REFUNDED}),
        },
        step=StepType.REFUND_SINGLE_USE_CARD,
        priority=Priority.RECOMMENDED,
        target_category="card_refund",
        target_train_type=None,
        # "1회용 교통카드 보증금 : 500원 (도착역에서 환급)"
        source="https://www.airportrailroad.com/train/normal/fare",
    ),
)

_GOAL_BY_CATEGORY = {"tickets": Goal.BUY_TICKET, "platform": Goal.BOARD_TRAIN, "exit": Goal.EXIT_STATION}


def goal_for(destination: Poi) -> Goal:
    """The goal a destination implies, until the agent states it explicitly."""
    return _GOAL_BY_CATEGORY.get(destination.category, Goal.FIND_FACILITY)


class PlanStep(BaseModel):
    step_id: str
    type: StepType
    priority: Priority
    status: StepStatus
    # Required steps are always part of the route; others only once the user accepts them.
    accepted: bool
    rule_id: str | None
    candidate_poi_ids: list[str]
    # Set when a route picks one candidate (or is the gate's far side for PASS_GATE).
    target_poi_id: str | None = None
    target_node_id: str | None = None

    @property
    def open(self) -> bool:
        return self.status in (StepStatus.PENDING, StepStatus.ACTIVE)

    @property
    def finished(self) -> bool:
        return self.status in (StepStatus.COMPLETED, StepStatus.SKIPPED)


class Plan(BaseModel):
    plan_id: str
    goal: Goal
    destination_poi_id: str
    steps: list[PlanStep]
    # Facts a rule needed but the session does not know; ask the user, then update the session.
    missing_facts: list[str]

    @computed_field
    @property
    def current_step_id(self) -> str | None:
        return next((s.step_id for s in self.steps if s.status == StepStatus.ACTIVE), None)

    @computed_field
    @property
    def completed(self) -> bool:
        return any(s.type == StepType.MOVE_TO_DESTINATION and s.status == StepStatus.COMPLETED for s in self.steps)

    def step(self, step_id: str) -> PlanStep:
        found = next((s for s in self.steps if s.step_id == step_id), None)
        if found is None:
            raise UnknownStep(step_id)
        return found

    def refresh_statuses(self) -> None:
        """Exactly one step is ACTIVE: the first accepted one still to do."""
        active = next((s for s in self.steps if s.open and s.accepted), None)
        for s in self.steps:
            if s.open:
                s.status = StepStatus.ACTIVE if s is active else StepStatus.PENDING


class UnknownStep(LookupError):
    pass


class StepNotAllowed(ValueError):
    pass


def build_plan(
    map_: MapData, goal: Goal, destination: Poi, facts: Facts, new_id: NewId, previous: Plan | None = None
) -> Plan:
    """Steps the rules call for, then the destination.

    Re-planning the same trip (e.g. after the user tells us their ticket type) keeps finished steps
    first, in the order they happened, and keeps the ids of steps that are still needed.
    """
    same_trip = previous if previous and previous.destination_poi_id == destination.poi_id else None
    history = [s for s in same_trip.steps if s.finished] if same_trip else []
    earlier = {(s.type, s.rule_id): s for s in same_trip.steps if not s.finished} if same_trip else {}
    finished_rules = {s.rule_id for s in history}
    values = {
        "ticket_type": facts.ticket_type,
        "refund_status": facts.refund_status,
        "train_type": destination.train_type or UNKNOWN,
    }

    steps: list[PlanStep] = []
    missing: list[str] = []
    for rule in (r for r in RULES if r.goal == goal and r.rule_id not in finished_rules):
        if any(values[f] != UNKNOWN and values[f] not in allowed for f, allowed in rule.when.items()):
            continue
        unknown = [f for f in rule.when if values[f] == UNKNOWN]
        if unknown:
            missing += [f for f in unknown if f not in missing]
            continue
        candidates = [p.poi_id for p in map_.open_pois(rule.target_category, rule.target_train_type)]
        step = earlier.get((rule.step, rule.rule_id)) or PlanStep(
            step_id=new_id("step"),
            type=rule.step,
            priority=rule.priority,
            status=StepStatus.PENDING,
            accepted=rule.priority == Priority.REQUIRED,
            rule_id=rule.rule_id,
            candidate_poi_ids=candidates,
        )
        step.candidate_poi_ids = candidates
        step.status = StepStatus.PENDING if candidates else StepStatus.BLOCKED  # nowhere open to do it
        steps.append(step)

    if not any(s.type == StepType.MOVE_TO_DESTINATION for s in history):
        arrive = earlier.get((StepType.MOVE_TO_DESTINATION, None)) or PlanStep(
            step_id=new_id("step"),
            type=StepType.MOVE_TO_DESTINATION,
            priority=Priority.REQUIRED,
            status=StepStatus.PENDING,
            accepted=True,
            rule_id=None,
            candidate_poi_ids=[destination.poi_id],
            target_poi_id=destination.poi_id,
            target_node_id=destination.node_id,
        )
        arrive.status = StepStatus.BLOCKED if destination.closed else StepStatus.PENDING
        steps.append(arrive)

    # Gate steps are not planned here: route_plan adds one for each gate the route crosses.
    plan = Plan(
        plan_id=same_trip.plan_id if same_trip else new_id("plan"),
        goal=goal,
        destination_poi_id=destination.poi_id,
        steps=[*history, *steps],
        missing_facts=missing,
    )
    plan.refresh_statuses()
    return plan


@dataclass(frozen=True)
class PlanRoute:
    route: PlannedRoute
    # For each route step, the plan step it serves (a PASS_GATE step for gates, else the stop ahead).
    plan_step_ids: list[str]


def route_plan(map_: MapData, plan: Plan, start_node_id: str, options: RouteOptions, new_id: NewId) -> PlanRoute | None:
    """Routes through every accepted, unfinished step and picks the stop for each.

    Updates the plan in place: chosen targets, and one PASS_GATE step per ticket gate the route
    crosses, placed before the stop it leads to. Returns None when the walk is impossible.
    """
    plan.steps = [s for s in plan.steps if not (s.type == StepType.PASS_GATE and s.open)]
    stops = [s for s in plan.steps if s.open and s.accepted]
    if not stops or stops[-1].type != StepType.MOVE_TO_DESTINATION:
        return None  # the destination itself is blocked or already reached

    candidates = [[map_.poi_by_id[p].node_id for p in s.candidate_poi_ids] for s in stops]
    route = plan_route_via(map_, start_node_id, candidates, options)
    if route is None:
        return None

    for step, node_id in zip(stops, route.stop_node_ids, strict=True):
        step.target_node_id = node_id
        step.target_poi_id = next(p for p in step.candidate_poi_ids if map_.poi_by_id[p].node_id == node_id)

    gates_before: dict[str, list[PlanStep]] = {}
    plan_step_ids: list[str] = []
    for route_step in route.steps:
        stop = stops[route_step.leg]
        if route_step.kind != "gate":
            plan_step_ids.append(stop.step_id)
            continue
        gate = PlanStep(
            step_id=new_id("step"),
            type=StepType.PASS_GATE,
            priority=Priority.REQUIRED,
            status=StepStatus.PENDING,
            accepted=True,
            rule_id=None,
            candidate_poi_ids=[],
            target_node_id=route_step.to_node_id,
        )
        gates_before.setdefault(stop.step_id, []).append(gate)
        plan_step_ids.append(gate.step_id)
    plan.steps = [g for s in plan.steps for g in (*gates_before.get(s.step_id, []), s)]
    plan.refresh_statuses()
    return PlanRoute(route=route, plan_step_ids=plan_step_ids)


@dataclass(frozen=True)
class Completion:
    """What finishing a step tells us about the user: where they stand and what they now hold."""

    node_id: str | None
    facts: Facts


def accept_step(plan: Plan, step_id: str) -> None:
    step = plan.step(step_id)
    if step.priority == Priority.REQUIRED or not step.open:
        raise StepNotAllowed("Only an open recommended or optional step can be accepted.")
    step.accepted = True
    plan.refresh_statuses()


def skip_step(plan: Plan, step_id: str) -> None:
    step = plan.step(step_id)
    if step.priority == Priority.REQUIRED or not step.open:
        raise StepNotAllowed("Only an open recommended or optional step can be skipped.")
    step.status = StepStatus.SKIPPED
    plan.refresh_statuses()


def complete_step(map_: MapData, plan: Plan, step_id: str, facts: Facts) -> Completion:
    step = plan.step(step_id)
    if step.status != StepStatus.ACTIVE:
        raise StepNotAllowed("Only the current step can be completed.")
    step.status = StepStatus.COMPLETED
    plan.refresh_statuses()

    updated = facts.model_copy()
    if step.type == StepType.BUY_TICKET:
        express = step.rule_id == "BUY_EXPRESS_TICKET"
        updated.ticket_type = TicketType.EXPRESS_TICKET if express else TicketType.SINGLE_USE
        if not express:
            updated.refund_status = RefundStatus.NOT_REFUNDED  # a new card carries the deposit
    elif step.type == StepType.REFUND_SINGLE_USE_CARD:
        updated.refund_status = RefundStatus.REFUNDED
    if step.target_node_id:
        updated.area_state = area_state_at(map_, step.target_node_id)
    return Completion(node_id=step.target_node_id, facts=updated)


def area_state_at(map_: MapData, node_id: str) -> AreaState:
    area = map_.node_by_id[node_id].area
    return AreaState(area.upper()) if area else AreaState.UNKNOWN
