"""Request and response bodies of the public REST API (API spec v0.3 §8 with system design v0.2 changes).

The OpenAPI document at /api/v1/docs is generated from these models, so this file is the contract the
client team builds against.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.contracts import AgentResponse
from app.domain.localization import Candidate
from app.domain.mapdata import Edge, Floor, Landmark, Lang, Node, Panorama, Position, Text, TrainType
from app.domain.planning import Facts, Plan, RefundStatus, TicketType

# Optimistic locking (API spec v0.3 §3): the revision the client last saw; a mismatch answers 409 REVISION_CONFLICT.
ExpectedRevision = Annotated[int, Field(description="직전 응답의 session.revision. 다르면 409 REVISION_CONFLICT")]


class SessionStatus(BaseModel):
    state: str
    revision: int


# --- sessions ---------------------------------------------------------------------------------


class ClientInfo(BaseModel):
    platform: str
    app_version: str
    ar_capable: bool = False


class CreateSessionIn(BaseModel):
    language: Lang = Field(description="안내 언어", examples=["ko"])
    map_id: str = Field(description="`GET /maps`의 지도 id", examples=["seoul-station"])
    client: ClientInfo


class CreateSessionOut(BaseModel):
    session_id: str
    session_token: str
    expires_in_s: int
    language: Lang
    map_id: str
    map_version: str
    session: SessionStatus


class Preferences(BaseModel):
    avoid_stairs: bool = Field(default=False, description="계단이 없는 경로만 사용")
    wheelchair: bool = Field(default=False, description="휠체어로 갈 수 있는 경로만 사용 (계단·에스컬레이터 제외)")


class PatchSessionIn(BaseModel):
    # Destinations are only set through /messages, so unknown fields such as a destination are rejected.
    model_config = ConfigDict(extra="forbid")

    expected_revision: ExpectedRevision
    language: Lang | None = None
    preferences: Preferences | None = None
    ticket_type: TicketType | None = Field(
        default=None, description="가진 승차권: TRANSIT_CARD · SINGLE_USE(1회용) · EXPRESS_TICKET(직통) · NONE"
    )
    refund_status: RefundStatus | None = Field(default=None, description="1회용 교통카드 보증금 환급 여부")


class PatchSessionOut(BaseModel):
    session_id: str
    language: Lang
    preferences: Preferences
    facts: Facts
    plan: Plan | None = Field(description="승차권 정보가 바뀌면 다시 세운 행동 계획")
    requires_route_refresh: bool
    session: SessionStatus


StateNextAction = Literal["reply", "locate", "confirm_location", "navigate", "continue_navigation", "none"]


class NavigationStateOut(BaseModel):
    session: SessionStatus
    destination_poi_id: str | None
    confirmed_node_id: str | None
    active_route_id: str | None
    route_revision: int | None
    map_version: str
    next_action: StateNextAction


class FeedbackIn(BaseModel):
    route_id: str | None = None
    rating: int = Field(ge=1, le=5, description="만족도 1~5")
    category: str = Field(max_length=32, description="예: wrong_direction, unclear_instruction, other")
    comment: str = Field(default="", max_length=2000)
    allow_image_retention: bool = Field(default=False, description="QA용 사진 7일 보관 동의")


class FeedbackOut(BaseModel):
    feedback_id: str
    status: Literal["received"] = "received"


# --- maps -------------------------------------------------------------------------------------


class MapSummary(BaseModel):
    map_id: str
    name: Text
    version: str
    floors: list[Floor]
    ar_supported: bool


class MapsOut(BaseModel):
    maps: list[MapSummary]


class GraphOut(BaseModel):
    map_id: str
    map_version: str
    coordinate_system: dict[str, str]
    floors: list[Floor]
    nodes: list[Node]
    edges: list[Edge]
    landmarks: list[Landmark]
    panoramas: list[Panorama]


class PoiOut(BaseModel):
    poi_id: str
    name_ko: str
    name_en: str
    category: str
    train_type: TrainType | None
    floor_id: str
    nearest_node_id: str
    aliases: list[str]
    verified: bool


class PoisOut(BaseModel):
    map_id: str
    map_version: str
    pois: list[PoiOut]


# --- conversation -----------------------------------------------------------------------------


class MessageIn(BaseModel):
    message: str = Field(default="", max_length=1000, description="사용자 문장", examples=["돈은 어떻게 내요?"])
    language: Lang | None = Field(default=None, description="이번 메시지부터 바꿀 언어 (선택)")
    selected_poi_id: str | None = Field(default=None, description="목적지 버튼·목록에서 고른 POI id (선택)")

    @model_validator(mode="after")
    def _not_empty(self) -> "MessageIn":
        if not self.message.strip() and not self.selected_poi_id:
            raise ValueError("message or selected_poi_id is required")
        return self


class MessageOut(AgentResponse):
    message_id: str
    plan: Plan | None = Field(description="현재 행동 계획. 목적지가 정해지면 생깁니다")
    session: SessionStatus


class ChatMessage(BaseModel):
    message_id: str
    role: Literal["user", "assistant"]
    created_at: datetime
    text: str
    selected_poi_id: str | None = Field(default=None, description="사용자가 버튼·목록으로 고른 목적지 (user)")
    has_image: bool = Field(default=False, description="사진을 붙여 보냈는지 (user). 사진 자체는 저장하지 않습니다")
    reply: AgentResponse | None = Field(default=None, description="보냈을 때와 같은 답변 전체 (assistant)")


class MessagesOut(BaseModel):
    messages: list[ChatMessage]
    session: SessionStatus


# --- location ---------------------------------------------------------------------------------


class LocalizationOut(BaseModel):
    localization_id: str
    status: Literal["needs_confirmation", "no_match"]
    map_version: str
    expires_in_s: int
    candidates: list[Candidate]
    retake_hint: str | None
    next_action: Literal["confirm_location", "recapture"]
    session: SessionStatus


class ConfirmationIn(BaseModel):
    expected_revision: ExpectedRevision
    # "marker" (API spec v0.3) belongs to the AR stage and is not accepted yet.
    method: Literal["photo_candidate", "manual"] = Field(
        description="photo_candidate: 사진 후보 중 선택 · manual: 목록에서 직접 선택"
    )
    localization_id: str | None = Field(default=None, description="photo_candidate일 때 `/localizations` 응답의 id")
    selected_node_id: str = Field(description="사용자가 고른 위치 노드, 예: line4-gates", examples=["line4-gates"])
    selected_heading_deg: float | None = Field(default=None, description="후보에 방향이 있으면 그 값")

    @model_validator(mode="after")
    def _photo_needs_localization(self) -> "ConfirmationIn":
        if self.method == "photo_candidate" and not self.localization_id:
            raise ValueError("localization_id is required for photo_candidate")
        return self


class ConfirmedLocation(BaseModel):
    map_id: str
    floor_id: str
    node_id: str
    heading_deg: float | None
    method: str


class ConfirmationOut(BaseModel):
    status: Literal["confirmed"] = "confirmed"
    location: ConfirmedLocation
    session: SessionStatus


# --- routes and navigation --------------------------------------------------------------------


class RouteStart(BaseModel):
    # "map_position" (API spec v0.3) needs AR alignment and is not accepted yet.
    type: Literal["confirmed_node"] = "confirmed_node"


class RouteIn(BaseModel):
    expected_revision: ExpectedRevision
    destination_poi_id: str = Field(description="`/messages`에서 정해진 목적지와 같아야 합니다")
    map_version: str = Field(description="앱이 가진 지도 버전")
    start: RouteStart = RouteStart()
    preferences: Preferences | None = Field(default=None, description="비우면 세션에 저장된 선호를 씁니다")
    reroute: bool = Field(default=False, description="길을 잃은 뒤 같은 목적지로 다시 계산할 때 true")


class Waypoint(BaseModel):
    waypoint_id: str
    floor_id: str
    position_map_m: Position


class RouteStepOut(BaseModel):
    step_id: str
    kind: str
    from_node_id: str
    to_node_id: str
    distance_m: float
    instruction_en: str
    instruction_ko: str
    pano_id: str | None
    arrow_yaw_deg: float | None
    plan_step_id: str | None = Field(description="이 구간이 향하는 행동 단계 (plan.steps의 step_id)")


class RouteOut(BaseModel):
    route_id: str
    route_revision: int
    status: Literal["created"] = "created"
    map_id: str
    map_version: str
    start_node_id: str
    destination_poi_id: str
    distance_m: float
    path_node_ids: list[str]
    waypoints: list[Waypoint]
    steps: list[RouteStepOut]
    plan: Plan = Field(
        description="이 경로가 거치는 행동 단계. 경로가 고른 장소(target_poi_id)와 개찰구 통과 단계가 채워집니다"
    )
    session: SessionStatus


# --- plans ------------------------------------------------------------------------------------


class TravelFacts(Facts):
    location_method: str | None = Field(description="현재 위치를 정한 방법: photo_candidate · manual · step_completed")


class PlanStateOut(BaseModel):
    plan: Plan | None = Field(description="목적지가 정해지기 전에는 null")
    facts: TravelFacts
    requires_route_refresh: bool = Field(
        description="true면 경로가 바뀌어야 하니 `/routes`를 `reroute: true`로 다시 부르세요"
    )
    session: SessionStatus


class StepActionIn(BaseModel):
    expected_revision: ExpectedRevision


NavigationEventType = Literal["navigation_started", "arrived", "navigation_cancelled"]


class NavigationEvent(BaseModel):
    event_id: str = Field(max_length=64, description="클라이언트가 만든 고유 id. 같은 id를 다시 보내면 무시합니다")
    type: NavigationEventType = Field(description="navigation_started · arrived · navigation_cancelled")
    client_timestamp: datetime
    context: dict[str, Any] = {}


class NavigationEventsIn(BaseModel):
    route_id: str | None = Field(default=None, description="현재 경로 id (`/routes` 응답의 route_id)")
    events: list[NavigationEvent] = Field(min_length=1, max_length=50)


class NavigationEventsOut(BaseModel):
    accepted_event_ids: list[str]
    session: SessionStatus


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    api_version: str
    dependencies: dict[str, str]
