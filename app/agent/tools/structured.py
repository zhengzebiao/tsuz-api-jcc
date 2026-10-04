"""Read-only structured JCC tools scoped to one immutable snapshot."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.tools.schemas import (
    EmptyInput,
    ExternalIdInput,
    SearchAdventuresInput,
    SearchAugmentsInput,
    SearchEquipmentInput,
    SearchGalaxiesInput,
    SearchHeroesInput,
    SearchTraitsInput,
    SourceRecord,
    ToolContext,
    ToolExecutionResult,
)
from app.jcc_data.models import (
    JccAdventure,
    JccAugment,
    JccEquipment,
    JccGalaxy,
    JccHero,
    JccTrait,
)


def _db(context: ToolContext) -> Session:
    return context.session_factory()


def _source(context: ToolContext, entity_type: str | None = None, entity_id: str | None = None) -> SourceRecord:
    snapshot = context.snapshot
    return SourceRecord(
        source_type="official_structured_data",
        snapshot_id=snapshot.snapshot_id,
        version=snapshot.version,
        entity_type=entity_type,
        entity_id=entity_id,
    )


def _metadata(context: ToolContext) -> dict[str, Any]:
    snapshot = context.snapshot
    return {
        "source_type": "official_structured_data",
        "snapshot_id": snapshot.snapshot_id,
        "mode": snapshot.mode,
        "season": snapshot.season,
        "version": snapshot.version,
        "revision": snapshot.revision,
        "content_hash": snapshot.content_hash,
    }


def _result(context: ToolContext, items: list[dict[str, Any]], *, total: int, entity_type: str) -> ToolExecutionResult:
    sources = tuple(
        _source(context, entity_type, str(item.get("external_id")))
        for item in items
        if item.get("external_id") is not None
    )
    return ToolExecutionResult(
        {"items": items, "total": total, "has_more": len(items) < total, **_metadata(context)}, sources
    )


def _serialize(model: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: getattr(model, field) for field in fields}


def get_snapshot_metadata(context: ToolContext, _input: EmptyInput) -> ToolExecutionResult:
    return ToolExecutionResult({"items": [], **_metadata(context)}, (_source(context, "snapshot", str(context.snapshot.snapshot_id)),))


def get_hero(context: ToolContext, input: ExternalIdInput) -> ToolExecutionResult:
    db = _db(context)
    try:
        item = db.scalar(
            select(JccHero).where(
                JccHero.snapshot_id == context.snapshot.snapshot_id,
                JccHero.external_id == input.external_id,
            )
        )
        items = [] if item is None else [_serialize(item, ("external_id", "name", "price", "hero_type", "health", "attack_damage", "armor", "magic_resist", "skill_name", "skill_description"))]
        return _result(context, items, total=len(items), entity_type="hero")
    finally:
        db.close()


def get_trait(context: ToolContext, input: ExternalIdInput) -> ToolExecutionResult:
    return _get_simple(context, JccTrait, input.external_id, "trait", ("external_id", "kind", "name", "max_level", "activation_list"))


def get_equipment(context: ToolContext, input: ExternalIdInput) -> ToolExecutionResult:
    return _get_simple(context, JccEquipment, input.external_id, "equipment", ("external_id", "name", "equipment_type", "basic_description", "description"))


def _get_simple(context: ToolContext, model: Any, external_id: str, entity_type: str, fields: tuple[str, ...]) -> ToolExecutionResult:
    db = _db(context)
    try:
        item = db.scalar(select(model).where(model.snapshot_id == context.snapshot.snapshot_id, model.external_id == external_id))
        items = [] if item is None else [_serialize(item, fields)]
        return _result(context, items, total=len(items), entity_type=entity_type)
    finally:
        db.close()


def _search(
    context: ToolContext,
    model: Any,
    input: Any,
    entity_type: str,
    fields: tuple[str, ...],
    filters: Callable[[Any], tuple[Any, ...]],
) -> ToolExecutionResult:
    db = _db(context)
    try:
        conditions = (model.snapshot_id == context.snapshot.snapshot_id, *filters(model))
        base = select(model).where(*conditions)
        total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        rows = list(db.scalars(base.order_by(model.external_id).limit(input.limit).offset(input.offset)))
        return _result(context, [_serialize(row, fields) for row in rows], total=total, entity_type=entity_type)
    finally:
        db.close()


def search_heroes(context: ToolContext, input: SearchHeroesInput) -> ToolExecutionResult:
    return _search(context, JccHero, input, "hero", ("external_id", "name", "price", "hero_type"), lambda m: tuple(
        item for item in (m.name.contains(input.name, autoescape=True) if input.name else None, m.price == input.price if input.price is not None else None) if item is not None
    ))


def search_traits(context: ToolContext, input: SearchTraitsInput) -> ToolExecutionResult:
    return _search(context, JccTrait, input, "trait", ("external_id", "kind", "name", "max_level"), lambda m: tuple(
        item for item in (m.kind == input.kind if input.kind else None, m.name.contains(input.name, autoescape=True) if input.name else None) if item is not None
    ))


def search_equipment(context: ToolContext, input: SearchEquipmentInput) -> ToolExecutionResult:
    return _search(context, JccEquipment, input, "equipment", ("external_id", "name", "equipment_type", "description"), lambda m: tuple(
        item for item in (m.name.contains(input.name, autoescape=True) if input.name else None, m.equipment_type == input.equipment_type if input.equipment_type else None) if item is not None
    ))


def search_augments(context: ToolContext, input: SearchAugmentsInput) -> ToolExecutionResult:
    return _search(context, JccAugment, input, "augment", ("external_id", "name", "level", "description"), lambda m: tuple(
        item for item in (m.name.contains(input.name, autoescape=True) if input.name else None, m.level == input.level if input.level is not None else None) if item is not None
    ))


def search_adventures(context: ToolContext, input: SearchAdventuresInput) -> ToolExecutionResult:
    return _search(context, JccAdventure, input, "adventure", ("external_id", "title", "price", "description"), lambda m: tuple(
        item for item in (m.title.contains(input.title, autoescape=True) if input.title else None, m.price == input.price if input.price is not None else None) if item is not None
    ))


def search_galaxies(context: ToolContext, input: SearchGalaxiesInput) -> ToolExecutionResult:
    return _search(context, JccGalaxy, input, "galaxy", ("external_id", "name", "description"), lambda m: tuple(
        item for item in (m.name.contains(input.name, autoescape=True) if input.name else None,) if item is not None
    ))
