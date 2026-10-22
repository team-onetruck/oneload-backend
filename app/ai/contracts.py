"""Contracts between the backend (R4) and the AI modules (R2 agent/vision, R3 RAG).

API spec v0.3 §10 and system design v0.2 §6. The backend only depends on these types, so an AI
implementation can be swapped (rule-based stand-in today, OpenAI-backed later) without touching
any endpoint.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel

from app.domain.localization import VisionFeatures
from app.domain.mapdata import Lang, MapData

Intent = Literal[
    "navigate_to_poi",
    "ticket_purchase_and_navigation",
    "payment_question",
    "usage_question",
    "report_off_route",
    "out_of_scope",
]
ReplyStatus = Literal[
    "answered", "needs_clarification", "destination_set", "relocalize", "cannot_answer", "out_of_scope"
]
NextAction = Literal["reply", "locate", "confirm_location", "navigate", "recapture", "none"]
TrainSlot = Literal["express", "allstop", "unknown"]
CardSlot = Literal["yes", "no", "unknown"]


class Slots(BaseModel):
    train_type: TrainSlot = "unknown"
    has_transit_card: CardSlot = "unknown"


class Source(BaseModel):
    document_id: str
    title: str
    source_url: str | None = None
    verified_at: str


class SuggestedReply(BaseModel):
    label: str
    selected_poi_id: str | None = None


class Destination(BaseModel):
    poi_id: str


class AgentResponse(BaseModel):
    status: ReplyStatus
    intent: Intent
    assistant_message: str
    items: list[str] = []  # steps or options, shown as a numbered list
    tip: str | None = None
    unverified_notes: list[str] = []  # facts not yet confirmed; the client marks them "Not verified"
    slots: Slots
    destination: Destination | None = None
    suggested_replies: list[SuggestedReply] = []
    sources: list[Source] = []
    next_action: NextAction


@dataclass(frozen=True)
class AgentContext:
    language: Lang
    map: MapData
    state: str
    destination_poi_id: str | None
    confirmed_node_id: str | None
    slots: Slots
    recent_messages: list[tuple[str, str]] = field(default_factory=list)  # (role, content), oldest first


@dataclass(frozen=True)
class AgentResult:
    response: AgentResponse
    # Document ids the agent actually retrieved this turn; the guard drops any cited source not in here.
    retrieved_document_ids: frozenset[str]


class Agent(Protocol):
    def handle_message(
        self, ctx: AgentContext, message: str, image: bytes | None, selected_poi_id: str | None
    ) -> AgentResult: ...


class VisionUnavailable(Exception):
    """The vision model cannot be reached or is not configured; the client falls back to manual selection."""


class VisionExtractor(Protocol):
    def extract(self, image: bytes) -> VisionFeatures: ...
