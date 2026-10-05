"""Protected persistence API for AI agent conversations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agent.events import AgentEvent, heartbeat
from app.agent.runtime import ConversationRuntimeManager
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
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import InProcessRateLimiter
from app.deps.auth import CurrentUser, require_scope

router = APIRouter(
    prefix="/api/agent",
    tags=["agent"],
    responses={401: {"description": "Invalid user token"}, 403: {"description": "Insufficient user scope"}},
)

_DB = Depends(get_db)
_USER = Depends(require_scope("jcc:agent:chat"))
_MESSAGE_LIMITER = InProcessRateLimiter(
    limit=settings.agent_rate_limit_messages,
    window_seconds=settings.agent_rate_limit_window_seconds,
)
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


def _status_event(item: AgentMessage, run_id: str) -> str:
    return AgentEvent(
        name=f"message.{item.status}",
        data={"message_id": item.id, "run_id": run_id, "status": item.status},
    ).sse()


def _message_response(item: AgentMessage, run_id: str | None = None) -> MessageResponse:
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
        run_id=run_id,
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


@router.get("/conversations/{conversation_id}/messages/{message_id}/events")
async def message_events(
    conversation_id: str,
    message_id: str,
    request: Request,
    current_user: CurrentUser = _USER,
    db: Session = _DB,
) -> StreamingResponse:
    try:
        message = service.get_message_for_user(
            db, user_id=current_user.user_id, conversation_id=conversation_id, message_id=message_id
        )
        run = service.get_run_for_user(
            db, user_id=current_user.user_id, conversation_id=conversation_id, message_id=message_id
        )
    except service.ConversationError as exc:
        raise _error(exc) from exc
    runtime: ConversationRuntimeManager | None = getattr(request.app.state, "agent_runtime", None)
    if runtime is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="agent runtime unavailable")
    last_event_id = request.headers.get("Last-Event-ID")
    try:
        replay = await runtime.replay(await runtime._runtime_for(conversation_id), last_event_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid Last-Event-ID") from exc
    conversation_runtime, subscriber = await runtime.subscribe(conversation_id)
    db.expire(message)
    db.expire(run)
    db.refresh(message)
    db.refresh(run)

    async def stream() -> AsyncIterator[str]:
        try:
            if replay:
                for emitted in replay:
                    yield emitted.sse()
                    if emitted.name in {"message.completed", "message.failed", "message.cancelled"}:
                        return
            else:
                yield _status_event(message, run.id)
            if message.status in {"completed", "failed", "cancelled"}:
                return
            while True:
                if await request.is_disconnected():
                    return
                try:
                    emitted = await asyncio.wait_for(
                        subscriber.get(), timeout=settings.agent_sse_heartbeat_seconds
                    )
                    yield emitted.sse()
                    if emitted.name in {"message.completed", "message.failed", "message.cancelled"}:
                        return
                except TimeoutError:
                    yield heartbeat()
        finally:
            await runtime.unsubscribe(conversation_runtime, subscriber)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/conversations/{conversation_id}/messages/{message_id}/cancel", response_model=MessageResponse)
async def cancel_message(
    conversation_id: str,
    message_id: str,
    request: Request,
    current_user: CurrentUser = _USER,
    db: Session = _DB,
) -> MessageResponse:
    try:
        message, run = service.request_cancel(
            db, user_id=current_user.user_id, conversation_id=conversation_id, message_id=message_id
        )
    except service.ConversationError as exc:
        raise _error(exc) from exc
    runtime: ConversationRuntimeManager | None = getattr(request.app.state, "agent_runtime", None)
    if runtime is not None:
        await runtime.cancel(conversation_id=conversation_id, message_id=message.id, run_id=run.id)
    return _message_response(message, run.id)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_message(
    conversation_id: str,
    payload: MessageCreate,
    request: Request,
    current_user: CurrentUser = _USER,
    db: Session = _DB,
) -> MessageAcceptedResponse:
    if settings.agent_rate_limit_enabled:
        decision = await _MESSAGE_LIMITER.check(f"user:{current_user.user_id}")
        if not decision.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "rate_limited", "retry_after_seconds": decision.retry_after_seconds},
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )
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
    run = service.ensure_run(db, message=message)
    db.commit()
    runtime: ConversationRuntimeManager | None = getattr(request.app.state, "agent_runtime", None)
    if (
        "agent_runtime" in request.app.state.__dict__
        and runtime is None
        and settings.llm_model
        and settings.llm_base_url
        and settings.llm_api_key
    ):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="agent runtime unavailable")
    if runtime is not None and run.status == "queued":
        try:
            # The message and run are committed before entering the in-process queue.
            await runtime.enqueue(message_id=message.id, run_id=run.id, conversation_id=conversation_id)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="agent runtime unavailable"
            ) from exc
    return MessageAcceptedResponse.model_validate(_message_response(message, run.id))
