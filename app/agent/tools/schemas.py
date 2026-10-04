"""Strict input and output contracts for Agent tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PaginationInput(StrictToolModel):
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=1000)


class EmptyInput(StrictToolModel):
    pass


class ExternalIdInput(StrictToolModel):
    external_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_:\-.]+$")


class SearchHeroesInput(PaginationInput):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    trait_id: str | None = Field(default=None, min_length=1, max_length=64)
    class_id: str | None = Field(default=None, min_length=1, max_length=64)
    price: int | None = Field(default=None, ge=1, le=10)


class SearchTraitsInput(PaginationInput):
    kind: str | None = Field(default=None, pattern=r"^(race|job)$")
    name: str | None = Field(default=None, min_length=1, max_length=100)


class SearchEquipmentInput(PaginationInput):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    equipment_type: str | None = Field(default=None, min_length=1, max_length=128)


class SearchAugmentsInput(PaginationInput):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    level: int | None = Field(default=None, ge=1, le=10)


class SearchAdventuresInput(PaginationInput):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    price: int | None = Field(default=None, ge=0, le=100)


class SearchGalaxiesInput(PaginationInput):
    name: str | None = Field(default=None, min_length=1, max_length=100)


class DeriveLineupInput(StrictToolModel):
    goal: str = Field(min_length=1, max_length=500)
    required_heroes: list[str] = Field(default_factory=list, max_length=8)
    candidate_limit: int = Field(default=3, ge=1, le=10)


@dataclass(frozen=True)
class SnapshotContext:
    snapshot_id: int
    mode: str
    season: str
    version: str
    revision: int
    content_hash: str


@dataclass(frozen=True)
class SourceRecord:
    source_type: str
    snapshot_id: int
    version: str
    entity_type: str | None = None
    entity_id: str | None = None
    rank: int | None = None
    excerpt: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ToolExecutionResult:
    output: dict[str, Any]
    sources: tuple[SourceRecord, ...] = ()


@dataclass(frozen=True)
class ToolContext:
    snapshot: SnapshotContext
    session_factory: Any
    cancel_event: Any | None = None
