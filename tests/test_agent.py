import re

from app.ai.contracts import AgentContext, AgentResponse, AgentResult, Destination, Slots, Source
from app.ai.guard import guard
from app.ai.rule_agent import RuleAgent


def _ask(seoul, message: str, slots: Slots | None = None, here: str | None = None, lang: str = "en") -> AgentResponse:
    ctx = AgentContext(
        language=lang, map=seoul, state="idle", destination_poi_id=None, confirmed_node_id=here, slots=slots or Slots()
    )
    return guard(RuleAgent().handle_message(ctx, message, None, None), seoul, lang)


def test_all_stop_asks_about_a_transit_card_before_choosing_a_destination(seoul):
    reply = _ask(seoul, "All-stop")

    assert reply.status == "needs_clarification"
    assert reply.destination is None
    assert reply.slots.train_type == "allstop"


def test_without_a_card_the_destination_is_the_ticket_machines(seoul):
    reply = _ask(seoul, "I don't have one", Slots(train_type="allstop"))

    assert reply.destination == Destination(poi_id="poi-allstop-tickets")
    assert reply.slots.has_transit_card == "no"
    assert "cash" in reply.tip
    assert reply.next_action == "locate"


def test_with_a_card_the_destination_is_the_platform(seoul):
    reply = _ask(seoul, "교통카드 있어요", Slots(train_type="allstop"), here="line4-gates", lang="ko")

    assert reply.destination == Destination(poi_id="poi-allstop-platform")
    assert reply.next_action == "navigate"  # location already confirmed, so no photo needed


def test_fare_questions_are_refused_without_inventing_numbers(seoul):
    reply = _ask(seoul, "How much is the express fare?")

    assert reply.status == "cannot_answer"
    assert not re.search(r"\d", reply.assistant_message)
    assert reply.sources == []


def test_payment_answer_flags_what_is_not_verified(seoul):
    reply = _ask(seoul, "Can I pay with a credit card?")

    assert reply.intent == "payment_question"
    assert reply.unverified_notes  # what the official pages do not cover is marked, not stated as fact


def _result(**fields) -> AgentResult:
    response = AgentResponse(
        status="destination_set",
        intent="navigate_to_poi",
        assistant_message="Go",
        slots=Slots(),
        next_action="locate",
        **fields,
    )
    return AgentResult(response, frozenset({"doc-real"}))


def test_guard_drops_a_destination_the_map_does_not_have(seoul):
    reply = guard(_result(destination=Destination(poi_id="poi-invented")), seoul, "en")

    assert reply.destination is None
    assert reply.status == "needs_clarification"


def test_guard_drops_sources_that_were_not_retrieved(seoul):
    cited = [
        Source(document_id="doc-real", title="r", verified_at="2026-10-01"),
        Source(document_id="doc-hallucinated", title="h", verified_at="2026-10-01"),
    ]

    reply = guard(_result(sources=cited), seoul, "en")

    assert [s.document_id for s in reply.sources] == ["doc-real"]
