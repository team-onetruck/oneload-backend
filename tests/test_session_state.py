import pytest

from app.domain.session_state import InvalidTransition, State, Trigger, transition

S, T = State, Trigger


@pytest.mark.parametrize(
    ("state", "trigger", "has_location", "expected"),
    [
        (S.IDLE, T.DESTINATION_SET, False, S.NEEDS_LOCATION),
        (S.ARRIVED, T.DESTINATION_SET, True, S.READY_TO_NAVIGATE),  # next leg starts where the user arrived
        (S.NAVIGATING, T.CLARIFICATION_ASKED, False, S.NEEDS_CLARIFICATION),
        (S.NAVIGATING, T.ANSWERED, False, S.NAVIGATING),  # questions mid-route do not drop the route
        (S.NAVIGATING, T.RELOCALIZE, False, S.NEEDS_LOCATION),
        (S.NEEDS_LOCATION, T.CANDIDATES_FOUND, False, S.NEEDS_CONFIRMATION),
        (S.NEEDS_CONFIRMATION, T.NO_MATCH, False, S.NEEDS_LOCATION),  # retake after a bad photo
        (S.NEEDS_CONFIRMATION, T.LOCATION_CONFIRMED, False, S.READY_TO_NAVIGATE),
        (S.NAVIGATING, T.ROUTE_CREATED, False, S.NAVIGATING),  # reroute keeps navigating
        (S.READY_TO_NAVIGATE, T.NAVIGATION_STARTED, False, S.NAVIGATING),
        (S.NAVIGATING, T.ARRIVED, False, S.ARRIVED),
        (S.NEEDS_CONFIRMATION, T.NAVIGATION_CANCELLED, False, S.IDLE),
    ],
)
def test_allowed_transitions(state, trigger, has_location, expected):
    assert transition(state, trigger, has_location=has_location) == expected


@pytest.mark.parametrize(
    ("state", "trigger"),
    [
        (S.IDLE, T.LOCATION_CONFIRMED),  # a location is only confirmed while locating
        (S.READY_TO_NAVIGATE, T.CANDIDATES_FOUND),
        (S.NEEDS_LOCATION, T.ROUTE_CREATED),  # no route before the location is confirmed
        (S.NEEDS_LOCATION, T.NAVIGATION_STARTED),
        (S.READY_TO_NAVIGATE, T.ARRIVED),  # cannot arrive without navigating
        (S.FAILED, T.ANSWERED),  # a failed session needs a new session
        (S.FAILED, T.NAVIGATION_CANCELLED),
    ],
)
def test_rejected_transitions(state, trigger):
    with pytest.raises(InvalidTransition):
        transition(state, trigger)
