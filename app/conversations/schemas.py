"""Pydantic contracts for the conversation API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

StrategyMode = Literal["gamble", "operation"]
ConversationStatus = Literal["active", "archived"]
MessageStatus = Literal[
    "queued",
    "running",
    "streaming",
    "completed",
    "failed",
    "cancelling",
    "cancelled",
]


def _require_non_blank(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    strategy_mode: StrategyMode = "gamble"

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _require_non_blank(value)


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    strategy_mode: StrategyMode | None = None

    @model_validator(mode="after")
    def validate_not_empty(self) -> "ConversationUpdate":
        if self.title is None and self.strategy_mode is None:
            raise ValueError("at least one field is required")
        return self

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return _require_non_blank(value) if value is not None else None


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=8000)
    strategy_mode: StrategyMode | None = None
    client_request_id: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        return _require_non_blank(value)

    @field_validator("client_request_id")
    @classmethod
    def validate_client_request_id(cls, value: str | None) -> str | None:
        return _require_non_blank(value) if value is not None else None


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    strategy_mode: StrategyMode
    status: ConversationStatus
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    status: MessageStatus
    sequence: int
    strategy_mode: StrategyMode
    client_request_id: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    run_id: None = None


class ConversationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ConversationResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class MessageListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[MessageResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class MessageAcceptedResponse(MessageResponse):
    """A persisted message; execution is intentionally deferred to phase two."""
