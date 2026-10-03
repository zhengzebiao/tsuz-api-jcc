"""Protected persistence API for AI agent conversations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.conversations import service
from app.conversations.models import AgentConversation, AgentMessage
from app.conversations.schemas import (
    ConversationCreate,
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdate,
    MessageAcceptedResponse,
    MessageCreate,
    MessageListResponse,
    MessageResponse,
)
from app.core.database import get_db
from app.deps.auth import CurrentUser, require_scope

router = APIRouter(
    prefix="/api/agent",
    tags=["agent"],
    responses={401: {"description": "Invalid user token"}, 403: {"description": "Insufficient user scope"}},
)

_DB = Depends(get_db)
_USER = Depends(require_scope("jcc:agent:chat"))
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def _error(exc: service.ConversationError) -> HTTPException:
    if isinstance(exc, service.ConversationNotFound):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    if isinstance(exc, service.ConversationArchived):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="conversation is archived")
    if isinstance(exc, service.IdempotencyConflict):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="client_request_id payload conflict")
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="conversation operation failed")


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _conversation_response(item: AgentConversation) -> ConversationResponse:
    return ConversationResponse(
        id=item.id,
        title=item.title,
        strategy_mode=item.strategy_mode,
        status=item.status,
        created_at=_utc_datetime(item.created_at),
        updated_at=_utc_datetime(item.updated_at),
        archived_at=_utc_datetime(item.archived_at),
    )


def _message_response(item: AgentMessage) -> MessageResponse:
    return MessageResponse(
        id=item.id,
        conversation_id=item.conversation_id,
        role=item.role,
        content=item.content,
        status=item.status,
        sequence=item.sequence,
        strategy_mode=item.strategy_mode,
        client_request_id=item.client_request_id,
        created_at=_utc_datetime(item.created_at),
        started_at=_utc_datetime(item.started_at),
        completed_at=_utc_datetime(item.completed_at),
    )


@router.post("/conversations", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate,
    current_user: CurrentUser = _USER,
    db: Session = _DB,
) -> ConversationResponse:
    return _conversation_response(
        service.create_conversation(
            db,
            user_id=current_user.user_id,
            title=payload.title,
            strategy_mode=payload.strategy_mode,
        )
    )


@router.get("/conversations", response_model=ConversationListResponse)
def list_conversations(
    limit: Limit = 50,
    offset: Offset = 0,
    current_user: CurrentUser = _USER,
    db: Session = _DB,
) -> ConversationListResponse:
    page = service.list_conversations(db, user_id=current_user.user_id, limit=limit, offset=offset)
    return ConversationListResponse(
        items=[_conversation_response(item) for item in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: str,
    current_user: CurrentUser = _USER,
    db: Session = _DB,
) -> ConversationResponse:
    try:
        return _conversation_response(
            service.get_conversation(db, user_id=current_user.user_id, conversation_id=conversation_id)
        )
    except service.ConversationError as exc:
        raise _error(exc) from exc


@router.patch("/conversations/{conversation_id}", response_model=ConversationResponse)
def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    current_user: CurrentUser = _USER,
    db: Session = _DB,
) -> ConversationResponse:
    try:
        return _conversation_response(
            service.update_conversation(
                db,
                user_id=current_user.user_id,
                conversation_id=conversation_id,
                title=payload.title,
                strategy_mode=payload.strategy_mode,
            )
        )
    except service.ConversationError as exc:
        raise _error(exc) from exc


@router.post("/conversations/{conversation_id}/archive", response_model=ConversationResponse)
def archive_conversation(
    conversation_id: str,
    current_user: CurrentUser = _USER,
    db: Session = _DB,
) -> ConversationResponse:
    try:
        return _conversation_response(
            service.archive_conversation(db, user_id=current_user.user_id, conversation_id=conversation_id)
        )
    except service.ConversationError as exc:
        raise _error(exc) from exc


@router.get("/conversations/{conversation_id}/messages", response_model=MessageListResponse)
def list_messages(
    conversation_id: str,
    limit: Limit = 50,
    offset: Offset = 0,
    current_user: CurrentUser = _USER,
    db: Session = _DB,
) -> MessageListResponse:
    try:
        page = service.list_messages(
            db,
            user_id=current_user.user_id,
            conversation_id=conversation_id,
            limit=limit,
            offset=offset,
        )
    except service.ConversationError as exc:
        raise _error(exc) from exc
    return MessageListResponse(
        items=[_message_response(item) for item in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_message(
    conversation_id: str,
    payload: MessageCreate,
    current_user: CurrentUser = _USER,
    db: Session = _DB,
) -> MessageAcceptedResponse:
    try:
        message = service.create_message(
            db,
            user_id=current_user.user_id,
            conversation_id=conversation_id,
            content=payload.content,
            strategy_mode=payload.strategy_mode,
            client_request_id=payload.client_request_id,
        )
    except service.ConversationError as exc:
        raise _error(exc) from exc
    return MessageAcceptedResponse.model_validate(_message_response(message))
