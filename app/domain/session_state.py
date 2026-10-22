"""Session state machine (API spec v0.3 §4, MVP subset without AR tracking states).

Every endpoint that changes a session asks this module for the next state instead of setting it
directly, so the whole lifecycle lives in one table.
"""

from enum import StrEnum


class State(StrEnum):
    IDLE = "idle"
    NEEDS_CLARIFICATION = "needs_clarification"
    NEEDS_LOCATION = "needs_location"
    NEEDS_CONFIRMATION = "needs_confirmation"
    READY_TO_NAVIGATE = "ready_to_navigate"
    NAVIGATING = "navigating"
    ARRIVED = "arrived"
    FAILED = "failed"


class Trigger(StrEnum):
    # Outcomes of POST /messages (the agent's response status).
    ANSWERED = "answered"
    CLARIFICATION_ASKED = "clarification_asked"
    DESTINATION_SET = "destination_set"
    RELOCALIZE = "relocalize"
    # POST /localizations and /location-confirmations.
    CANDIDATES_FOUND = "candidates_found"
    NO_MATCH = "no_match"
    LOCATION_CONFIRMED = "location_confirmed"
    # POST /routes and /navigation-events.
    ROUTE_CREATED = "route_created"
    NAVIGATION_STARTED = "navigation_started"
    ARRIVED = "arrived"
    NAVIGATION_CANCELLED = "navigation_cancelled"


class InvalidTransition(Exception):
    def __init__(self, state: State, trigger: Trigger):
        super().__init__(f"{trigger} is not allowed in state {state}")
        self.state = state
        self.trigger = trigger


_ACTIVE = frozenset(State) - {State.FAILED}
_LOCATING = frozenset({State.NEEDS_LOCATION, State.NEEDS_CONFIRMATION})
_SAME = None  # target meaning "stay in the current state"

# trigger -> (states it is allowed from, target state)
# Conversation is allowed in every active state: a user may ask how to pay or change their mind at
# any point, so message outcomes never fail with INVALID_STATE.
_TABLE: dict[Trigger, tuple[frozenset[State], State | None]] = {
    Trigger.ANSWERED: (_ACTIVE, _SAME),
    Trigger.CLARIFICATION_ASKED: (_ACTIVE, State.NEEDS_CLARIFICATION),
    Trigger.DESTINATION_SET: (_ACTIVE, _SAME),  # resolved by has_location, see transition()
    Trigger.RELOCALIZE: (_ACTIVE, State.NEEDS_LOCATION),
    Trigger.CANDIDATES_FOUND: (_LOCATING, State.NEEDS_CONFIRMATION),
    Trigger.NO_MATCH: (_LOCATING, State.NEEDS_LOCATION),
    Trigger.LOCATION_CONFIRMED: (_LOCATING, State.READY_TO_NAVIGATE),
    Trigger.ROUTE_CREATED: (frozenset({State.READY_TO_NAVIGATE, State.NAVIGATING}), _SAME),
    Trigger.NAVIGATION_STARTED: (frozenset({State.READY_TO_NAVIGATE}), State.NAVIGATING),
    Trigger.ARRIVED: (frozenset({State.NAVIGATING}), State.ARRIVED),
    Trigger.NAVIGATION_CANCELLED: (_ACTIVE, State.IDLE),
}


def allows(state: State, trigger: Trigger) -> bool:
    return state in _TABLE[trigger][0]


def transition(state: State, trigger: Trigger, *, has_location: bool = False) -> State:
    allowed, target = _TABLE[trigger]
    if state not in allowed:
        raise InvalidTransition(state, trigger)
    if trigger is Trigger.DESTINATION_SET:
        return State.READY_TO_NAVIGATE if has_location else State.NEEDS_LOCATION
    return state if target is _SAME else target
