"""Business rules for conversation and message persistence."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.conversations import repository
from app.conversations.models import AgentConversation, AgentMessage


class ConversationError(Exception):
    """Base error translated by the API layer."""


class ConversationNotFound(ConversationError):
    pass


class ConversationArchived(ConversationError):
    pass


class IdempotencyConflict(ConversationError):
    pass


@contextmanager
def _transaction(db: Session) -> Iterator[None]:
    if db.in_transaction():
        yield
        return
    with db.begin():
        yield


def create_conversation(db: Session, *, user_id: str, title: str, strategy_mode: str) -> AgentConversation:
    with _transaction(db):
        return repository.create_conversation(
            db,
            user_id=user_id,
            title=title,
            strategy_mode=strategy_mode,
        )


def get_conversation(db: Session, *, user_id: str, conversation_id: str) -> AgentConversation:
    conversation = repository.get_conversation(db, user_id, conversation_id)
    if conversation is None:
        raise ConversationNotFound
    return conversation


def list_conversations(db: Session, *, user_id: str, limit: int, offset: int) -> repository.Page:
    return repository.list_conversations(db, user_id, limit=limit, offset=offset)


def update_conversation(
    db: Session,
    *,
    user_id: str,
    conversation_id: str,
    title: str | None,
    strategy_mode: str | None,
) -> AgentConversation:
    with _transaction(db):
        conversation = get_conversation(db, user_id=user_id, conversation_id=conversation_id)
        repository.lock_conversation(db, conversation)
        if conversation.status == "archived":
            raise ConversationArchived
        return repository.update_conversation(
            db,
            conversation,
            title=title,
            strategy_mode=strategy_mode,
        )


def archive_conversation(db: Session, *, user_id: str, conversation_id: str) -> AgentConversation:
    with _transaction(db):
        conversation = get_conversation(db, user_id=user_id, conversation_id=conversation_id)
        repository.lock_conversation(db, conversation)
        return repository.archive_conversation(db, conversation)


def list_messages(db: Session, *, user_id: str, conversation_id: str, limit: int, offset: int) -> repository.Page:
    # Resolve the owner before exposing any message data.
    get_conversation(db, user_id=user_id, conversation_id=conversation_id)
    return repository.list_messages(db, conversation_id=conversation_id, limit=limit, offset=offset)


def _same_request(message: AgentMessage, *, content: str, strategy_mode: str) -> bool:
    return message.content == content and message.strategy_mode == strategy_mode


def create_message(
    db: Session,
    *,
    user_id: str,
    conversation_id: str,
    content: str,
    strategy_mode: str | None,
    client_request_id: str | None,
) -> AgentMessage:
    with _transaction(db):
        conversation = get_conversation(db, user_id=user_id, conversation_id=conversation_id)
        repository.lock_conversation(db, conversation)
        if conversation.status == "archived":
            raise ConversationArchived
        effective_mode = strategy_mode or conversation.strategy_mode

        # Inspect idempotency and allocate sequence while the same conversation
        # lock is held on PostgreSQL.
        repository.lock_conversation(db, conversation)
        if client_request_id is not None:
            existing = repository.get_message_by_request_id(
                db,
                conversation_id=conversation.id,
                client_request_id=client_request_id,
            )
            if existing is not None:
                if not _same_request(existing, content=content, strategy_mode=effective_mode):
                    raise IdempotencyConflict
                return existing

        try:
            # Keep a uniqueness failure inside a savepoint so the outer transaction
            # remains usable when resolving a concurrent idempotency retry.
            with db.begin_nested():
                message = repository.create_message(
                    db,
                    conversation=conversation,
                    user_id=user_id,
                    content=content,
                    strategy_mode=effective_mode,
                    client_request_id=client_request_id,
                )
            return message
        except IntegrityError:
            # A concurrent retry may have won the unique request key race. Resolve
            # it into the same idempotent response instead of leaking SQL details.
            if client_request_id is not None:
                existing = repository.get_message_by_request_id(
                    db,
                    conversation_id=conversation.id,
                    client_request_id=client_request_id,
                )
                if existing is not None and _same_request(existing, content=content, strategy_mode=effective_mode):
                    return existing
            raise
