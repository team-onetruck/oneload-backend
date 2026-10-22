"""Output guard (system design v0.2 §5.5): structural checks on whatever the agent returns.

Pydantic already rejects unknown enum values; this module checks the things a schema cannot:
that ids point at real, verified data and that cited sources were actually retrieved.
"""

from app.ai.contracts import AgentResponse, AgentResult
from app.domain.mapdata import Lang, MapData

_NO_SOURCE_NOTE = {
    "en": "I couldn't attach a verified source to this answer.",
    "ko": "이 답변에는 확인된 출처를 붙이지 못했어요.",
}
_UNKNOWN_DESTINATION = {
    "en": "I couldn't find that place on the verified map. Where would you like to go?",
    "ko": "확인된 지도에서 그 장소를 찾지 못했어요. 어디로 가고 싶으세요?",
}


def guard(result: AgentResult, map_: MapData, lang: Lang) -> AgentResponse:
    response = result.response.model_copy(deep=True)

    if response.destination and not map_.verified_poi(response.destination.poi_id):
        # The agent must never be able to invent a destination id.
        response.destination = None
        response.status = "needs_clarification"
        response.next_action = "reply"
        response.assistant_message = _UNKNOWN_DESTINATION[lang]

    for reply in response.suggested_replies:
        if reply.selected_poi_id and not map_.verified_poi(reply.selected_poi_id):
            reply.selected_poi_id = None

    cited = len(response.sources)
    response.sources = [s for s in response.sources if s.document_id in result.retrieved_document_ids]
    if cited and not response.sources:
        response.unverified_notes.append(_NO_SOURCE_NOTE[lang])

    return response
