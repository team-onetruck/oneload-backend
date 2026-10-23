"""Swagger UI (/api/v1/docs): Korean descriptions and the parts FastAPI cannot infer from the endpoints."""

from enum import StrEnum
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi


class Tag(StrEnum):
    HEALTH = "상태 점검"
    SESSIONS = "세션"
    MAPS = "지도"
    CONVERSATION = "대화"
    LOCATION = "위치 확인"
    ROUTES = "경로"
    PLAN = "행동 계획"


_TAG_DESCRIPTIONS = {
    Tag.HEALTH: "서버와 DB가 살아 있는지 확인합니다.",
    Tag.SESSIONS: "세션 생성, 설정 변경, 길안내 진행 이벤트, 피드백. 세션 토큰이 필요합니다.",
    Tag.MAPS: "공개 지도 데이터(노드·통로·POI·거리뷰 사진). 토큰 없이 조회합니다.",
    Tag.CONVERSATION: "자연어 질문과 목적지 지정의 단일 진입점입니다.",
    Tag.LOCATION: "사진으로 위치 후보를 찾고, 사용자가 직접 확정합니다.",
    Tag.ROUTES: "확정된 위치에서 목적지까지 단계별 경로를 계산합니다.",
    Tag.PLAN: (
        "목적지에 가기 위해 해야 할 일(표 사기, 개찰구 통과, 보증금 환급, 도착)의 순서와 진행 상태입니다."
        " 규칙은 공식 안내에 근거한 것만 쓰고, 판단에 필요한 정보가 없으면 `missing_facts`로 알려줍니다."
    ),
}

DESCRIPTION = """
서울역 공항철도(AREX) 길안내 API입니다. API 명세 v0.3에 시스템 설계서 v0.2의 변경(거리뷰, 사진 첨부 대화,
`tip`·`unverified_notes`)을 반영했습니다.

**Swagger에서 대화 테스트**

1. `POST /sessions`에 아래 본문을 넣고 **Execute**를 누릅니다.
   `{"language": "ko", "map_id": "seoul-station", "client": {"platform": "web", "app_version": "swagger"}}`
2. 응답의 `session_token`을 복사해 오른쪽 위 **Authorize**에 붙여넣습니다.
3. `POST /sessions/{session_id}/messages`를 열고 `session_id`, 새 `Idempotency-Key` UUID,
   본문 `{"message": "공항티켓 어디서 구매해?"}`를 넣어 **Execute**를 누릅니다.
4. 응답의 `assistant_message`, `suggested_replies`, `destination`, `sources`를 확인합니다.

모든 `POST`에는 요청마다 새 `Idempotency-Key`가 필요합니다. 같은 키와 같은 본문을 다시 보내면 저장된 응답을
돌려주고, 첫 요청이 처리 중이면 `409 REQUEST_IN_PROGRESS`가 옵니다.

대표 길안내 흐름은 `messages` → `location-confirmations` → `routes` → `navigation-events`입니다.
목적지가 정해지면 행동 계획(`plan`)이 함께 생기고, 단계를 마칠 때마다 `steps/{step_id}/complete`로 알립니다.
상태를 바꾸는 요청에는 직전 응답의 `session.revision`을 `expected_revision`으로 넣습니다.

**현재 제한**

- 지식 검색은 아직 R3 서비스가 아니라 패키지에 포함된 데모 문서를 사용합니다.
- 지도 데이터는 공항철도 층 정보(직통 표 B2, 일반 표 B3, 승강장 B7)만 확인된 값이고, 나머지는 현장 답사 전 임시값입니다.

**오류 형식**: 모든 오류는 `{"error": {"code", "message", "retryable", "details"}, "request_id"}` 모양입니다.
"""


def _description(app: FastAPI) -> str:
    agent = type(app.state.agent).__name__
    vision = type(app.state.vision).__name__
    return (
        f"{DESCRIPTION}\n\n**현재 AI 구성**\n\n"
        f"- 대화: `{agent}` (`LLMAgent`는 OpenAI, `RuleAgent`는 키 미설정 시 대체 구현)\n"
        f"- 사진: `{vision}` (`LLMVision`은 OpenAI, `NoVision`은 503 후 수동 위치 선택)\n"
    )


_IDEMPOTENCY_HEADER = {
    "name": "Idempotency-Key",
    "in": "header",
    "required": True,
    "description": (
        "요청마다 새 UUID. 같은 키와 같은 본문으로 다시 보내면 저장된 응답을 돌려주고,"
        " 첫 요청이 아직 처리 중이면 409 REQUEST_IN_PROGRESS(Retry-After: 1)를 돌려줍니다."
    ),
    "schema": {"type": "string", "maxLength": 64},
}

_RESPONSE_TEXT = {"Successful Response": "성공", "Validation Error": "입력 형식 오류 (code: INVALID_INPUT)"}


def install_openapi(app: FastAPI) -> None:
    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = get_openapi(
                title=app.title,
                version=app.version,
                description=_description(app),
                routes=app.routes,
                tags=[{"name": tag, "description": text} for tag, text in _TAG_DESCRIPTIONS.items()],
            )
            for path in schema["paths"].values():
                # The header is enforced by IdempotencyMiddleware, so FastAPI does not see it on any endpoint.
                if "post" in path:
                    path["post"].setdefault("parameters", []).append(_IDEMPOTENCY_HEADER)
                for operation in path.values():
                    for response in operation.get("responses", {}).values():
                        response["description"] = _RESPONSE_TEXT.get(response["description"], response["description"])
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
