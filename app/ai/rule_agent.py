"""Deterministic stand-in for the conversational agent.

It answers the Seoul Station / AREX scenario with keyword rules so the API, the client and the
state machine can be built and tested before the OpenAI-backed agent (R2) exists. It honours the
same contract and the same rules: only verified POIs, cited sources, unconfirmed facts flagged.
"""

import re
from collections.abc import Callable
from typing import Literal

from app.ai.contracts import (
    AgentContext,
    AgentResponse,
    AgentResult,
    Destination,
    Intent,
    ReplyStatus,
    Slots,
    Source,
    SuggestedReply,
)
from app.domain.mapdata import Poi

EXPRESS_TICKETS = "poi-express-tickets"
ALLSTOP_TICKETS = "poi-allstop-tickets"
EXPRESS_PLATFORM = "poi-express-platform"
ALLSTOP_PLATFORM = "poi-allstop-platform"

Tip = Literal["express_pay", "tap_card", "buy_single"]
Say = Callable[[str, str], str]

# (document_id, title en, title ko, url, verified_at). The doc-* entries are R3's knowledge documents
# (oneload-rag/knowledge, same ids and titles), so a fallback answer cites what the LLM agent would.
_SOURCES = {
    "floors": (
        "arex-official-seoul-station",
        "AREX official station guide: Seoul",
        "공항철도 공식 역 안내: 서울역",
        "https://www.airportrailroad.com/train/normal/info/010/0",
        "2026-10-23",
    ),
    "train_types": (
        "doc-arex-train-types",
        "AREX train types: Express and All-stop",
        "공항철도 열차 종류 - 직통열차와 일반열차",
        "https://www.airportrailroad.com/train/express/introduce",
        "2026-10-22",
    ),
    "express_ticket": (
        "doc-express-ticket",
        "Buying an Express train ticket at Seoul Station",
        "직통열차 승차권 구매 (서울역)",
        "https://www.airportrailroad.com/train/express/introduce",
        "2026-10-23",
    ),
    "allstop_ticket": (
        "doc-allstop-ticket",
        "Riding the All-stop train: transit cards and single-journey tickets",
        "일반열차 타는 방법 - 교통카드와 1회용 교통카드",
        "https://www.airportrailroad.com/train/normal/introduce",
        "2026-10-23",
    ),
    "deposit": (
        "doc-deposit-refund",
        "Getting the single-journey ticket deposit back",
        "1회용 교통카드 보증금 환급",
        "https://news.seoul.go.kr/traffic/archives/1763",
        "2026-10-22",
    ),
}


class RuleAgent:
    def handle_message(
        self, ctx: AgentContext, message: str, image: bytes | None, selected_poi_id: str | None
    ) -> AgentResult:
        response = _respond(ctx, message, image is not None, selected_poi_id)
        return AgentResult(response, frozenset(s.document_id for s in response.sources))


def _respond(ctx: AgentContext, message: str, has_image: bool, selected_poi_id: str | None) -> AgentResponse:
    lang = ctx.language
    say: Say = (lambda en, _ko: en) if lang == "en" else (lambda _en, ko: ko)
    slots = ctx.slots.model_copy()
    text = message.lower()

    def has(*words: str) -> bool:
        # English on word boundaries ("press" must not match "express"); Korean by substring, since
        # particles attach directly to the word ("교통카드가").
        return any(re.search(rf"\b{re.escape(w)}\b", text) if w.isascii() else w in text for w in words)

    if selected_poi_id and (poi := ctx.map.verified_poi(selected_poi_id)):
        slots.train_type = poi.train_type or slots.train_type
        return _destination(ctx, say, slots, poi, _tip_for(poi, slots), "navigate_to_poi")

    awaiting_card = slots.train_type == "allstop" and slots.has_transit_card == "unknown"
    if awaiting_card and has("don't", "do not", "no", "없어", "없음", "아니"):
        slots.has_transit_card = "no"
        return _destination(ctx, say, slots, ctx.map.poi_by_id[ALLSTOP_TICKETS], "buy_single", "payment_question")
    if awaiting_card and has("i have", "have one", "yes", "있어", "있음", "네"):
        slots.has_transit_card = "yes"
        return _destination(ctx, say, slots, ctx.map.poi_by_id[ALLSTOP_PLATFORM], "tap_card", "payment_question")

    if has("fare", "price", "how much", "요금", "얼마", "가격"):
        return _fare(say, slots)
    if has("lost", "wrong", "잘못", "길을 잃", "헤매"):
        return _reply(
            say,
            slots,
            "relocalize",
            "report_off_route",
            "No problem. I won't assume where you are; let's check again.",
            "괜찮아요. 위치를 짐작하지 않고 다시 확인할게요.",
            next_action="locate",
        )
    if has("recharge", "top up", "top-up", "충전"):
        return _recharge(say, slots)
    if has("deposit", "refund", "보증금", "환급"):
        return _deposit(say, slots)
    if has("t-money", "tmoney", "transit card", "교통카드", "티머니"):
        return _what_card(say, slots)
    if has("pay", "cash", "credit", "card", "결제", "현금", "카드", "돈"):
        return _pay(say, slots)
    if has("press", "buy", "purchase", "ticket", "구매", "눌러", "사요", "승차권") or (
        has_image and ctx.confirmed_node_id
    ):
        return _buy(say, slots, has_image) if slots.train_type != "unknown" else _train_types(say, slots)
    if has("platform", "승강장", "타는 곳"):
        if slots.train_type == "unknown":
            return _train_types(say, slots)
        poi_id = EXPRESS_PLATFORM if slots.train_type == "express" else ALLSTOP_PLATFORM
        poi = ctx.map.poi_by_id[poi_id]
        return _destination(ctx, say, slots, poi, _tip_for(poi, slots), "navigate_to_poi")
    if has("express", "직통"):
        slots.train_type = "express"
        return _destination(
            ctx, say, slots, ctx.map.poi_by_id[EXPRESS_TICKETS], "express_pay", "ticket_purchase_and_navigation"
        )
    if has("all-stop", "all stop", "allstop", "regular", "일반"):
        slots.train_type = "allstop"
        return _ask_card(say, slots)
    if has("airport", "incheon", "icn", "arex", "공항", "인천"):
        return _train_types(say, slots)
    if has("not sure", "which", "difference", "모르", "차이"):
        return _not_sure(say, slots)
    return _out_of_scope(say, slots)


# --- reply builders --------------------------------------------------------------------------


def _reply(say: Say, slots: Slots, status: ReplyStatus, intent: Intent, en: str, ko: str, **fields) -> AgentResponse:
    fields.setdefault("next_action", "reply" if status == "needs_clarification" else "none")
    return AgentResponse(status=status, intent=intent, assistant_message=say(en, ko), slots=slots, **fields)


def _source(say: Say, key: str) -> Source:
    doc_id, title_en, title_ko, url, verified_at = _SOURCES[key]
    return Source(document_id=doc_id, title=say(title_en, title_ko), source_url=url, verified_at=verified_at)


def _option(say: Say, en: str, ko: str, poi_id: str | None = None) -> SuggestedReply:
    return SuggestedReply(label=say(en, ko), selected_poi_id=poi_id)


def _tip_for(poi: Poi, slots: Slots) -> Tip | None:
    if poi.poi_id == EXPRESS_TICKETS:
        return "express_pay"
    if poi.poi_id == ALLSTOP_TICKETS:
        return "buy_single"
    if poi.poi_id == ALLSTOP_PLATFORM and slots.has_transit_card == "yes":
        return "tap_card"
    return None


_TIPS: dict[Tip, tuple[str, str]] = {
    "express_pay": ("You can pay by card or cash at the counter.", "창구에서 카드나 현금으로 낼 수 있어요."),
    "tap_card": (
        "No ticket needed: tap your card at the All-stop gate.",
        "표는 필요 없어요. 일반열차 개찰구에서 카드를 찍으세요.",
    ),
    "buy_single": (
        "At the machine, choose Single journey. It may only take cash, so have some won ready.",
        "발매기에서 1회용 교통카드를 고르세요. 현금만 될 수 있으니 원화를 준비하세요.",
    ),
}


def _destination(ctx: AgentContext, say: Say, slots: Slots, poi: Poi, tip: Tip | None, intent: Intent) -> AgentResponse:
    floor = ctx.map.node_by_id[poi.node_id].floor_id
    name = poi.name(ctx.language)
    if ctx.confirmed_node_id:
        here = ctx.map.node_by_id[ctx.confirmed_node_id].name(ctx.language)
        en = f"Got it: the {name}, on {floor}. You're at the {here}, so we can go now."
        ko = f"{name}(으)로 안내할게요. {floor}에 있어요. 지금 {here}에 있으니 바로 출발할 수 있어요."
        next_action = "navigate"
    else:
        en = f"I'll take you to the {name}, on {floor}. First, take a photo of a nearby sign and confirm where you are."
        ko = f"{name}(으)로 안내할게요. {floor}에 있어요. 먼저 주변 안내판을 찍어서 지금 위치를 확인해주세요."
        next_action = "locate"
    return _reply(
        say,
        slots,
        "destination_set",
        intent,
        en,
        ko,
        destination=Destination(poi_id=poi.poi_id),
        tip=say(*_TIPS[tip]) if tip else None,
        sources=[_source(say, "floors")],
        next_action=next_action,
    )


def _train_types(say: Say, slots: Slots) -> AgentResponse:
    return _reply(
        say,
        slots,
        "needs_clarification",
        "ticket_purchase_and_navigation",
        "Two Airport Railroad (AREX) trains go to Incheon Airport. "
        "They are paid for differently. Which one do you want?",
        "인천공항까지는 공항철도 열차가 두 종류예요. 내는 방법이 달라요. 어떤 열차로 갈까요?",
        items=[
            say(
                "Express: non-stop, every seat reserved. You buy a ticket.",
                "직통열차: 무정차, 전 좌석 지정석. 승차권을 사야 해요.",
            ),
            say(
                "All-stop: stops at every station. You tap a transit card, like the subway.",
                "일반열차: 모든 역에 정차. 지하철처럼 교통카드를 찍어요.",
            ),
        ],
        suggested_replies=[
            _option(say, "Express", "직통열차"),
            _option(say, "All-stop", "일반열차"),
            _option(say, "Not sure", "잘 모르겠어요"),
        ],
        sources=[_source(say, "train_types")],
    )


def _not_sure(say: Say, slots: Slots) -> AgentResponse:
    return _reply(
        say,
        slots,
        "needs_clarification",
        "ticket_purchase_and_navigation",
        "A simple way to decide:",
        "이렇게 고르면 쉬워요.",
        items=[
            say(
                "Lots of luggage, or you want a guaranteed seat: Express.",
                "짐이 많거나 꼭 앉아서 가고 싶다면: 직통열차",
            ),
            say(
                "You have (or will get) a transit card and don't mind stops: All-stop.",
                "교통카드가 있거나 살 거고, 정차해도 괜찮다면: 일반열차",
            ),
        ],
        unverified_notes=[
            say(
                "I don't have verified fares or travel times, so I won't compare them.",
                "확인된 요금·소요시간이 없어서 비교하지 않을게요.",
            )
        ],
        suggested_replies=[_option(say, "Express", "직통열차"), _option(say, "All-stop", "일반열차")],
        sources=[_source(say, "train_types")],
    )


def _ask_card(say: Say, slots: Slots) -> AgentResponse:
    return _reply(
        say,
        slots,
        "needs_clarification",
        "payment_question",
        "For the All-stop train you tap a transit card at the gate. Do you have one, like T-money?",
        "일반열차는 개찰구에서 교통카드를 찍어요. 티머니 같은 교통카드가 있나요?",
        suggested_replies=[
            _option(say, "I have a transit card", "교통카드 있어요"),
            _option(say, "I don't have one", "없어요"),
            _option(say, "What's a transit card?", "교통카드가 뭐예요?"),
        ],
        sources=[_source(say, "allstop_ticket")],
    )


def _pay(say: Say, slots: Slots) -> AgentResponse:
    return _reply(
        say,
        slots,
        "answered",
        "payment_question",
        "It depends on the train:",
        "열차에 따라 달라요.",
        items=[
            say(
                "Express: buy a ticket on B2, at the Express customer center or a ticket machine. "
                "Machines take cash or credit cards, including foreign ones.",
                "직통열차: B2 직통열차 고객안내센터나 발매기에서 승차권 구매. 발매기는 현금·신용카드(해외 카드 포함).",
            ),
            say(
                "All-stop with a transit card: just tap it at the gate on B3.",
                "일반열차 + 교통카드: B3 개찰구에서 카드만 찍으면 돼요.",
            ),
            say(
                "All-stop without a card: buy a single-journey ticket at the machine on B3, by cash or credit card.",
                "일반열차 + 카드 없음: B3 발매기에서 1회용 교통카드를 사요. 현금이나 신용카드로 낼 수 있어요.",
            ),
        ],
        unverified_notes=[
            say(
                "Which payment methods the Express counter itself accepts is not confirmed yet.",
                "직통열차 창구에서 쓸 수 있는 결제 수단은 아직 확인 전이에요.",
            )
        ],
        suggested_replies=[
            _option(say, "Express", "직통열차"),
            _option(say, "All-stop", "일반열차"),
            _option(say, "What's a transit card?", "교통카드가 뭐예요?"),
        ],
        sources=[_source(say, "floors"), _source(say, "express_ticket"), _source(say, "allstop_ticket")],
    )


def _what_card(say: Say, slots: Slots) -> AgentResponse:
    return _reply(
        say,
        slots,
        "answered",
        "payment_question",
        "A transit card (T-money is the common one) is a rechargeable card "
        "for the subway, buses and the AREX All-stop train.",
        "교통카드(대표적으로 티머니)는 지하철·버스·공항철도 일반열차에 쓰는 충전식 카드예요.",
        items=[
            say(
                "Buy one at a convenience store or a transit card machine.",
                "편의점이나 교통카드 자판기에서 살 수 있어요.",
            ),
            say("Top it up at a ticket machine in the station.", "역의 승차권 발매기에서 충전해요."),
            say("Tap it when you enter and again when you leave.", "들어갈 때 한 번, 나올 때 한 번 찍어요."),
        ],
        unverified_notes=[
            say(
                "Where to buy one inside this part of Seoul Station is not registered yet.",
                "서울역 이 구역 안의 판매 위치는 아직 등록 전이에요.",
            )
        ],
        suggested_replies=[
            _option(say, "I have a transit card", "교통카드 있어요"),
            _option(say, "I don't have one", "없어요"),
            _option(say, "How do I top it up?", "충전은 어떻게 해요?"),
        ],
        sources=[_source(say, "allstop_ticket")],
    )


def _recharge(say: Say, slots: Slots) -> AgentResponse:
    return _reply(
        say,
        slots,
        "answered",
        "payment_question",
        "To top up a transit card:",
        "교통카드 충전 방법:",
        items=[
            say("Find a recharge machine near the gates.", "개찰구 근처 충전기를 찾으세요."),
            say("Put the card on the reader.", "카드를 올려놓으세요."),
            say("Choose an amount and insert cash.", "금액을 고르고 현금을 넣으세요."),
        ],
        unverified_notes=[
            say(
                "I can't confirm card payment for top-ups here. Plan on cash.",
                "여기서 카드로 충전되는지는 확인할 수 없어요. 현금을 준비하세요.",
            )
        ],
        sources=[_source(say, "allstop_ticket")],
    )


def _deposit(say: Say, slots: Slots) -> AgentResponse:
    return _reply(
        say,
        slots,
        "answered",
        "usage_question",
        "A single-journey ticket has a deposit you get back after your trip:",
        "1회용 교통카드는 보증금이 있고, 이용 후 돌려받을 수 있어요.",
        items=[
            say(
                "Find a deposit refund machine near the gates where you get off.",
                "내린 역 개찰구 근처의 보증금 환급기를 찾으세요.",
            ),
            say("Put the card in the slot.", "카드를 투입구에 넣으세요."),
            say("Take the coins that come out.", "나오는 동전을 받으세요."),
        ],
        unverified_notes=[
            say(
                "Refund machine locations aren't on the map yet, so I can't guide you to one.",
                "환급기 위치는 아직 지도에 없어서 길안내는 할 수 없어요.",
            )
        ],
        sources=[_source(say, "deposit")],
    )


def _fare(say: Say, slots: Slots) -> AgentResponse:
    return _reply(
        say,
        slots,
        "cannot_answer",
        "usage_question",
        "I don't have a verified fare, so I won't guess. "
        "The price is shown on the ticket machine screen, or ask at the counter.",
        "확인된 요금 정보가 없어서 추측하지 않을게요. 발매기 화면에 가격이 나오고, 창구에서도 물어볼 수 있어요.",
        suggested_replies=[_option(say, "How do I pay?", "돈은 어떻게 내요?")],
    )


def _buy(say: Say, slots: Slots, has_image: bool) -> AgentResponse:
    express = slots.train_type == "express"
    steps = (
        [
            say("Switch the screen to English if needed.", "필요하면 화면 언어를 바꾸세요."),
            say("Choose Express train.", "직통열차를 선택하세요."),
            say("Choose how many people.", "인원을 선택하세요."),
            say("Pay by card or cash, then take your ticket.", "카드나 현금으로 결제하고 승차권을 받으세요."),
        ]
        if express
        else [
            say("Switch the screen to English.", "화면 언어를 바꾸세요."),
            say("Choose Single journey ticket.", "1회용 교통카드를 고르세요."),
            say("Choose Incheon Airport as your destination.", "목적지로 인천공항을 고르세요."),
            say("Pay by cash or credit card and take the card.", "현금이나 신용카드로 결제하고 카드를 받으세요."),
        ]
    )
    intro_en, intro_ko = (
        ("To buy an Express ticket:", "직통열차 승차권은 이렇게 사요.")
        if express
        else ("For the All-stop train without a transit card:", "교통카드 없이 일반열차를 탈 때는:")
    )
    seen_en, seen_ko = (
        ("I can see a ticket machine in your photo. ", "사진에서 발매기가 보여요. ") if has_image else ("", "")
    )
    return _reply(
        say,
        slots,
        "answered",
        "usage_question",
        seen_en + intro_en,
        seen_ko + intro_ko,
        items=steps,
        unverified_notes=[
            say(
                "Exact button names and positions aren't verified. "
                "If the screen looks different, ask the staff nearby.",
                "버튼 이름과 위치는 확인 전이에요. 화면이 다르면 근처 직원에게 물어보세요.",
            )
        ],
        suggested_replies=[_option(say, "Go to the platform", "승강장으로 가기")],
        sources=[_source(say, "express_ticket" if express else "allstop_ticket")],
    )


def _out_of_scope(say: Say, slots: Slots) -> AgentResponse:
    return _reply(
        say,
        slots,
        "out_of_scope",
        "out_of_scope",
        "Right now I cover the Airport Railroad at Seoul Station: tickets, cards, cash and the way to the platform.",
        "지금은 서울역 공항철도 구역을 도와드려요. 표·카드·현금, 승강장 가는 길을 물어보세요.",
        suggested_replies=[
            _option(say, "Incheon Airport", "인천공항"),
            _option(say, "How do I pay?", "돈은 어떻게 내요?"),
            _option(say, "What's a transit card?", "교통카드가 뭐예요?"),
        ],
    )
