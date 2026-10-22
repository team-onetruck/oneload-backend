"""Request and response bodies of the public REST API (API spec v0.3 §8 with system design v0.2 changes).

The OpenAPI document at /docs is generated from these models, so this file is the contract the
client team builds against.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.ai.contracts import AgentResponse
from app.domain.localization import Candidate
from app.domain.mapdata import Edge, Floor, Landmark, Lang, Node, Panorama, Position, Text, TrainType


class SessionStatus(BaseModel):
    state: str
    revision: int


# --- sessions ---------------------------------------------------------------------------------


class ClientInfo(BaseModel):
    platform: str
    app_version: str
    ar_capable: bool = False


class CreateSessionIn(BaseModel):
    language: Lang
    map_id: str
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
    avoid_stairs: bool = False


class PatchSessionIn(BaseModel):
    # Destinations are only set through /messages, so unknown fields such as a destination are rejected.
    model_config = ConfigDict(extra="forbid")

    expected_revision: int
    language: Lang | None = None
    preferences: Preferences | None = None


class PatchSessionOut(BaseModel):
    session_id: str
    language: Lang
    preferences: Preferences
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
    rating: int = Field(ge=1, le=5)
    category: str = Field(max_length=32)
    comment: str = Field(default="", max_length=2000)
    allow_image_retention: bool = False


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
    message: str = Field(default="", max_length=1000)
    language: Lang | None = None
    selected_poi_id: str | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> "MessageIn":
        if not self.message.strip() and not self.selected_poi_id:
            raise ValueError("message or selected_poi_id is required")
        return self


class MessageOut(AgentResponse):
    message_id: str
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
    expected_revision: int
    # "marker" (API spec v0.3) belongs to the AR stage and is not accepted yet.
    method: Literal["photo_candidate", "manual"]
    localization_id: str | None = None
    selected_node_id: str
    selected_heading_deg: float | None = None

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
    expected_revision: int
    destination_poi_id: str
    map_version: str
    start: RouteStart = RouteStart()
    preferences: Preferences | None = None
    reroute: bool = False


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
    session: SessionStatus


NavigationEventType = Literal["navigation_started", "arrived", "navigation_cancelled"]


class NavigationEvent(BaseModel):
    event_id: str = Field(max_length=64)
    type: NavigationEventType
    client_timestamp: datetime
    context: dict[str, Any] = {}


class NavigationEventsIn(BaseModel):
    route_id: str | None = None
    events: list[NavigationEvent] = Field(min_length=1, max_length=50)


class NavigationEventsOut(BaseModel):
    accepted_event_ids: list[str]
    session: SessionStatus


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    api_version: str
    dependencies: dict[str, str]
