from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy import select
from starlette.datastructures import UploadFile

from app.ai.contracts import AgentContext, Slots
from app.ai.guard import guard
from app.api.deps import AgentDep, CurrentSession, Db, Maps, advance, map_of, read_jpeg, status_of
from app.config import get_settings
from app.domain.session_state import Trigger
from app.errors import ApiError, ErrorCode
from app.models import MessageRow, SessionRow
from app.schemas import MessageIn, MessageOut
from app.security import new_id

router = APIRouter(tags=["conversation"])

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
                        "image": {"type": "string", "format": "binary", "description": "JPEG, ≤2 MB"},
                    },
                }
            },
        },
    }
}


@router.post("/sessions/{session_id}/messages", openapi_extra=_BODY_DOC)
def send_message(
    parsed: Annotated[tuple[MessageIn, bytes | None], Depends(read_message)],
    row: CurrentSession,
    db: Db,
    maps: Maps,
    agent: AgentDep,
) -> MessageOut:
    """Main entry point: questions, destination choices (selected_poi_id) and "I'm lost" all go through here."""
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
    if response.status == "relocalize":
        row.confirmed_node_id = None
    if response.destination:
        row.destination_poi_id = response.destination.poi_id
        row.active_route_id = None
    advance(row, _TRIGGER[response.status], has_location=row.confirmed_node_id is not None)

    message_id = new_id("msg")
    db.add_all(
        [
            MessageRow(
                id=new_id("msg"), session_id=row.id, role="user", content=body.message or f"[{body.selected_poi_id}]"
            ),
            MessageRow(id=message_id, session_id=row.id, role="assistant", content=response.assistant_message),
        ]
    )
    return MessageOut(**response.model_dump(), message_id=message_id, session=status_of(row))


def _recent_messages(db: Db, row: SessionRow) -> list[tuple[str, str]]:
    rows = db.scalars(
        select(MessageRow)
        .where(MessageRow.session_id == row.id)
        .order_by(MessageRow.seq.desc())
        .limit(get_settings().recent_messages)
    ).all()
    return [(m.role, m.content) for m in reversed(rows)]
