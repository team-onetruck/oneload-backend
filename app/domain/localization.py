"""Turns what a photo shows into location candidates (system design v0.2 §5.4).

The vision model only reports what it can read (VisionFeatures). Which node that means is decided
here, against registered landmarks, so a model can never invent a location.
"""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from app.domain.mapdata import Lang, MapData

ImageQuality = Literal["usable", "blurry", "too_dark", "no_signage", "too_close"]
TextKind = Literal["line_name", "line_number", "exit_number", "facility_name", "direction", "other"]

MAX_CANDIDATES = 3
_HINT_BONUS = 0.5


class VisionText(BaseModel):
    text: str
    lang: str | None = None
    kind: TextKind = "other"


class VisionFeatures(BaseModel):
    image_quality: ImageQuality
    scene_type: str | None = None
    texts: list[VisionText] = []
    facilities: list[str] = []
    person_visible: bool = False


class Candidate(BaseModel):
    node_id: str
    floor_id: str
    rank_score: float  # relative ordering only; never shown to users as a probability
    heading_deg: float | None = None
    landmark_id: str
    evidence: list[str]


@dataclass(frozen=True)
class Hints:
    floor_hint: str | None = None
    last_node_id: str | None = None


def match_location(features: VisionFeatures, map_: MapData, hints: Hints, lang: Lang) -> list[Candidate]:
    if features.image_quality != "usable":
        return []
    seen = {_normalise(t.text) for t in features.texts}
    neighbours = _neighbours(map_, hints.last_node_id)

    # A place can carry several signs; it is one candidate scored on every distinct text it shows.
    matched_at: dict[str, dict[str, str]] = {}  # node id -> normalised text -> text as on the sign
    best_sign: dict[str, tuple[int, str]] = {}  # node id -> (texts matched, landmark id)
    for landmark in map_.landmarks:
        matched = [s for s in landmark.sign_texts if _normalise(s) in seen]
        if not matched:
            continue
        texts = matched_at.setdefault(landmark.node_id, {})
        for s in matched:
            texts.setdefault(_normalise(s), s)
        if len(matched) > best_sign.get(landmark.node_id, (0, ""))[0]:
            best_sign[landmark.node_id] = (len(matched), landmark.landmark_id)

    scored: list[tuple[float, Candidate]] = []
    for node_id, texts in matched_at.items():
        node = map_.node_by_id[node_id]
        score = len(texts)
        score += _HINT_BONUS if hints.floor_hint == node.floor_id else 0
        score += _HINT_BONUS if node.id in neighbours else 0
        evidence = [f"Sign reads “{s}”" if lang == "en" else f"“{s}” 안내판" for s in texts.values()]
        scored.append(
            (
                score,
                Candidate(
                    node_id=node.id,
                    floor_id=node.floor_id,
                    rank_score=round(score / (score + 1), 2),
                    landmark_id=best_sign[node_id][1],
                    evidence=evidence,
                ),
            )
        )

    scored.sort(key=lambda item: item[0], reverse=True)
    return [candidate for _, candidate in scored[:MAX_CANDIDATES]]


_RETAKE_HINTS: dict[ImageQuality, tuple[str, str]] = {
    "blurry": ("The photo is blurry. Hold still and try again.", "사진이 흐려요. 잠시 멈춰서 다시 찍어주세요."),
    "too_dark": ("The photo is too dark. Try facing a lit sign.", "사진이 어두워요. 밝은 안내판 쪽을 찍어주세요."),
    "too_close": ("Step back a little so the whole sign fits.", "안내판 전체가 나오게 조금 뒤로 물러나 찍어주세요."),
    "no_signage": (
        "I can't see a sign. Try a wider shot with a line name or station sign.",
        "안내판이 보이지 않아요. 노선 이름이나 역 안내판이 보이게 넓게 찍어주세요.",
    ),
    "usable": (
        "I can't tell where this is. Try a wider shot with a line name or station sign.",
        "위치를 알아보기 어려워요. 노선 이름이나 역 안내판이 보이게 넓게 찍어주세요.",
    ),
}


def retake_hint(quality: ImageQuality, lang: Lang) -> str:
    en, ko = _RETAKE_HINTS[quality]
    return en if lang == "en" else ko


def _normalise(text: str) -> str:
    return re.sub(r"[\s\-·.,'’]", "", text).lower()


def _neighbours(map_: MapData, node_id: str | None) -> set[str]:
    if node_id is None:
        return set()
    return {e.to for e in map_.edges if e.from_ == node_id} | {e.from_ for e in map_.edges if e.to == node_id}
