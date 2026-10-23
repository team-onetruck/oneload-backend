from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import ValidationError
from sqlalchemy import select
from starlette.datastructures import UploadFile

from app.ai.contracts import AgentContext, AgentResponse, Slots
from app.ai.guard import guard
from app.api.deps import (
    AgentDep,
    CurrentSession,
    Db,
    Maps,
    advance,
    map_of,
    plan_of,
    read_jpeg,
    replan,
    status_of,
)
from app.config import get_settings
from app.domain.planning import RefundStatus, TicketType
from app.domain.session_state import Trigger
from app.errors import ApiError, ErrorCode
from app.models import MessageRow, SessionRow
from app.openapi import Tag
from app.schemas import ChatMessage, MessageIn, MessageOut, MessagesOut
from app.security import new_id

router = APIRouter(tags=[Tag.CONVERSATION])

_TRIGGER = {
    "answered": Trigger.ANSWERED,
    "cannot_answer": Trigger.ANSWERED,
    "out_of_scope": Trigger.ANSWERED,
    "needs_clarification": Trigger.CLARIFICATION_ASKED,
    "destination_set": Trigger.DESTINATION_SET,
    "relocalize": Trigger.RELOCALIZE,
}


async def read_message(request: Request) -> tuple[MessageIn, bytes | None]:
    """JSON for text, multipart when a photo is attached (e.g. a ticket machine: "What should I press?")."""
    try:
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            form = await request.form()
            upload = form.get("image")
            image = read_jpeg(upload) if isinstance(upload, UploadFile) else None
            fields = {k: v for k, v in form.items() if k != "image" and isinstance(v, str)}
            return MessageIn.model_validate(fields), image
        return MessageIn.model_validate_json(await request.body()), None
    except ValidationError as exc:
        raise ApiError(
            ErrorCode.INVALID_INPUT,
            "Request body is invalid.",
            {"errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]},
        ) from exc


_BODY_DOC = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": MessageIn.model_json_schema()},
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "properties": {
                        **MessageIn.model_json_schema()["properties"],
                        "image": {
                            "type": "string",
                            "format": "binary",
                            "description": "JPEG, 2MB 이하. 저장하지 않습니다.",
                        },
                    },
                }
            },
        },
    }
}


@router.post(
    "/sessions/{session_id}/messages",
    openapi_extra=_BODY_DOC,
    summary="메시지 보내기 (핵심 진입점)",
    description=(
        '질문, 목적지 선택, "길을 잃었어요"가 모두 여기로 옵니다. 텍스트는 JSON으로, 사진을 붙일 때(예: 발매기 사진 +'
        ' "뭘 눌러요?")는 multipart로 보냅니다. 버튼으로 목적지를 고르면 `selected_poi_id`를 함께 보냅니다.\n\n'
        "응답의 `status`가 세션 상태를 바꿉니다: `needs_clarification`(추가 질문), `destination_set`(목적지 확정 →"
        ' 위치가 없으면 needs_location), `relocalize`(위치 다시 확인). `unverified_notes`는 화면에 "확인되지 않음"으로'
        " 구분해 보여줘야 합니다."
    ),
)
def send_message(
    parsed: Annotated[tuple[MessageIn, bytes | None], Depends(read_message)],
    row: CurrentSession,
    db: Db,
    maps: Maps,
    agent: AgentDep,
) -> MessageOut:
    body, image = parsed
    map_ = map_of(row, maps)
    if body.language:
        row.language = body.language

    ctx = AgentContext(
        language=row.language,
        map=map_,
        state=row.state,
        destination_poi_id=row.destination_poi_id,
        confirmed_node_id=row.confirmed_node_id,
        slots=Slots(**row.slots),
        recent_messages=_recent_messages(db, row),
    )
    response = guard(agent.handle_message(ctx, body.message, image, body.selected_poi_id), map_, row.language)

    row.slots = response.slots.model_dump()
    learned = _learn_facts(row, response.slots)
    if response.status == "relocalize":
        row.confirmed_node_id = None
        row.location_method = None
    if response.destination:
        row.destination_poi_id = response.destination.poi_id
        row.active_route_id = None
    plan = replan(row, map_) if response.destination or learned else plan_of(row)
    advance(row, _TRIGGER[response.status], has_location=row.confirmed_node_id is not None)

    message_id = new_id("msg")
    db.add_all(
        [
            MessageRow(
                id=new_id("msg"),
                session_id=row.id,
                role="user",
                content=body.message or f"[{body.selected_poi_id}]",
                payload={
                    "message": body.message,
                    "selected_poi_id": body.selected_poi_id,
                    "has_image": image is not None,
                },
            ),
            MessageRow(
                id=message_id,
                session_id=row.id,
                role="assistant",
                content=response.assistant_message,
                payload=response.model_dump(mode="json"),
            ),
        ]
    )
    return MessageOut(**response.model_dump(), message_id=message_id, plan=plan, session=status_of(row))


@router.get(
    "/sessions/{session_id}/messages",
    summary="대화 기록 조회",
    description=(
        "앱을 다시 열거나 연결이 끊겼다 돌아왔을 때 화면을 복구합니다. 최근 메시지를 오래된 순서로 돌려주며,"
        " 한길의 답변은 보냈을 때와 같은 모양(`reply`: 목록·tip·확인되지 않음·출처·추천 답변)으로 들어 있습니다."
        " 길안내 진행 상태는 `navigation-state`로 함께 복구하세요."
    ),
)
def list_messages(
    row: CurrentSession,
    db: Db,
    limit: Annotated[int, Query(ge=1, le=100, description="최근 몇 개를 받을지")] = 50,
) -> MessagesOut:
    rows = db.scalars(
        select(MessageRow).where(MessageRow.session_id == row.id).order_by(MessageRow.seq.desc()).limit(limit)
    ).all()
    return MessagesOut(messages=[_history_item(m) for m in reversed(rows)], session=status_of(row))


def _history_item(m: MessageRow) -> ChatMessage:
    payload = m.payload or {}
    if m.role == "assistant":
        return ChatMessage(
            message_id=m.id,
            role="assistant",
            created_at=m.created_at,
            text=m.content,
            reply=AgentResponse.model_validate(payload) if payload else None,
        )
    return ChatMessage(
        message_id=m.id,
        role="user",
        created_at=m.created_at,
        text=payload.get("message", m.content),
        selected_poi_id=payload.get("selected_poi_id"),
        has_image=payload.get("has_image", False),
    )


def _recent_messages(db: Db, row: SessionRow) -> list[tuple[str, str]]:
    rows = db.scalars(
        select(MessageRow)
        .where(MessageRow.session_id == row.id)
        .order_by(MessageRow.seq.desc())
        .limit(get_settings().recent_messages)
    ).all()
    return [(m.role, m.content) for m in reversed(rows)]


def _learn_facts(row: SessionRow, slots: Slots) -> bool:
    """What the user told the agent about their ticket fills facts the planner did not know yet.

    Facts the client already set (PATCH /sessions) are never overwritten. Returns True if any changed.
    """
    changed = False
    if row.ticket_type == TicketType.UNKNOWN:
        if slots.has_single_use_card == "yes":
            ticket = TicketType.SINGLE_USE
        else:
            ticket = {"yes": TicketType.TRANSIT_CARD, "no": TicketType.NONE}.get(slots.has_transit_card)
        if ticket:
            row.ticket_type, changed = ticket, True
    if row.refund_status == RefundStatus.UNKNOWN:
        refund = {"yes": RefundStatus.REFUNDED, "no": RefundStatus.NOT_REFUNDED}.get(slots.single_use_card_returned)
        if refund:
            row.refund_status, changed = refund, True
    return changed
