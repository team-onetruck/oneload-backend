"""Indoor map data: one JSON file per map, loaded once at startup.

The file format mirrors the GET /maps/{map_id}/graph response (API spec v0.3 §8.3) plus the
street-view additions from the system design v0.2 (edge headings, panoramas).
"""

from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Lang = Literal["en", "ko"]
TrainType = Literal["express", "allstop"]
EdgeType = Literal["corridor", "escalator", "stairs", "elevator", "gate"]


class Text(BaseModel):
    en: str
    ko: str

    def __call__(self, lang: Lang) -> str:
        return self.en if lang == "en" else self.ko


class Position(BaseModel):
    x: float
    y: float
    z: float


class Floor(BaseModel):
    floor_id: str
    elevation_m: float


class Node(BaseModel):
    id: str
    floor_id: str
    position: Position
    name: Text


class Edge(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    type: EdgeType
    distance_m: float
    heading_deg: float
    instruction: Text


class Poi(BaseModel):
    poi_id: str
    node_id: str
    category: str
    train_type: TrainType | None = None
    name: Text
    aliases: list[str] = []
    verified: bool


class Landmark(BaseModel):
    landmark_id: str
    node_id: str
    sign_texts: list[str]


class Panorama(BaseModel):
    pano_id: str
    node_id: str
    image_url: str
    north_yaw_deg: float
    captured_at: str | None
    is_example: bool


class MapData(BaseModel):
    map_id: str
    version: str
    name: Text
    provenance: dict[str, str]
    coordinate_system: dict[str, str]
    floors: list[Floor]
    nodes: list[Node]
    edges: list[Edge]
    pois: list[Poi]
    landmarks: list[Landmark]
    panoramas: list[Panorama]

    @model_validator(mode="after")
    def _references_exist(self) -> "MapData":
        # The map file is edited by hand after each survey; fail at startup, not mid-route.
        nodes = {n.id for n in self.nodes}
        floors = {f.floor_id for f in self.floors}
        refs = [
            *((f"node {n.id} floor", n.floor_id, floors) for n in self.nodes),
            *((f"edge {e.from_}->{e.to}", end, nodes) for e in self.edges for end in (e.from_, e.to)),
            *((f"poi {p.poi_id}", p.node_id, nodes) for p in self.pois),
            *((f"landmark {lm.landmark_id}", lm.node_id, nodes) for lm in self.landmarks),
            *((f"panorama {p.pano_id}", p.node_id, nodes) for p in self.panoramas),
        ]
        missing = [f"{owner}: {ref}" for owner, ref, known in refs if ref not in known]
        if missing:
            raise ValueError(f"map {self.map_id} has unknown references: {missing}")
        return self

    @cached_property
    def node_by_id(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}

    @cached_property
    def poi_by_id(self) -> dict[str, Poi]:
        return {p.poi_id: p for p in self.pois}

    @cached_property
    def panorama_by_node(self) -> dict[str, Panorama]:
        return {p.node_id: p for p in self.panoramas}

    def verified_poi(self, poi_id: str) -> Poi | None:
        """POIs the agent may set as a destination; unverified ones never reach a user."""
        poi = self.poi_by_id.get(poi_id)
        return poi if poi and poi.verified else None

    def search_pois(self, query: str = "") -> list[Poi]:
        q = query.strip().lower()
        verified = [p for p in self.pois if p.verified]
        if not q:
            return verified
        return [p for p in verified if any(q in s.lower() for s in (p.name.en, p.name.ko, *p.aliases))]


class MapStore:
    """All maps in a directory, keyed by map_id. Read-only after startup."""

    def __init__(self, maps: list[MapData]):
        self._maps = {m.map_id: m for m in maps}

    @classmethod
    def load(cls, directory: Path) -> "MapStore":
        return cls([MapData.model_validate_json(p.read_text()) for p in sorted(directory.glob("*.json"))])

    def get(self, map_id: str) -> MapData | None:
        return self._maps.get(map_id)

    def all(self) -> list[MapData]:
        return list(self._maps.values())
