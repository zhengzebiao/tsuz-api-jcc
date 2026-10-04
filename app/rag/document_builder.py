"""Build deterministic semantic documents from one immutable JCC snapshot."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.jcc_data.models import JccAdventure, JccAugment, JccEquipment, JccGalaxy, JccHero, JccSnapshot, JccTrait

DOCUMENT_SCHEMA_VERSION = "rag-document-v1"


@dataclass(frozen=True)
class NormalizedDocument:
    document_id: str
    entity_type: str
    entity_id: str
    section: str
    snapshot_id: int
    mode: str
    season: str
    version: str
    revision: int
    content: str
    content_hash: str
    metadata: dict[str, Any]


def _text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(unicodedata.normalize("NFC", str(value)).split())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content(fields: list[tuple[str, Any]]) -> str:
    return "\n".join(f"{label}: {_text(value)}" for label, value in fields if _text(value))


def _document(snapshot: JccSnapshot, entity_type: str, entity_id: str, fields: list[tuple[str, Any]], metadata: dict[str, Any]) -> NormalizedDocument:
    content = _content(fields)
    digest = hashlib.sha256(f"{DOCUMENT_SCHEMA_VERSION}\n{content}".encode()).hexdigest()
    section = f"{entity_type}_overview"
    return NormalizedDocument(
        document_id=f"{entity_type}:{entity_id}:{section}", entity_type=entity_type, entity_id=entity_id,
        section=section, snapshot_id=snapshot.id, mode=snapshot.mode, season=snapshot.season,
        version=snapshot.version, revision=snapshot.revision, content=content, content_hash=digest,
        metadata={"document_schema_version": DOCUMENT_SCHEMA_VERSION, **metadata},
    )


def build_snapshot_documents(db: Session, snapshot: JccSnapshot) -> tuple[NormalizedDocument, ...]:
    """Return stable entity-sized documents; database primary keys never enter content."""
    result: list[NormalizedDocument] = []
    common = {"snapshot_id": snapshot.id, "mode": snapshot.mode, "season": snapshot.season, "version": snapshot.version}
    heroes = db.scalars(select(JccHero).where(JccHero.snapshot_id == snapshot.id).order_by(JccHero.external_id)).all()
    for row in heroes:
        result.append(_document(snapshot, "hero", row.external_id, [
            ("英雄", row.name), ("费用", row.price), ("类型", row.hero_type), ("生命值", row.health),
            ("攻击力", row.attack_damage), ("护甲", row.armor), ("魔抗", row.magic_resist),
            ("技能", row.skill_name), ("技能说明", row.skill_description), ("技能数值", _json(row.skill_values) if row.skill_values else None),
        ], {**common, "source_file": "chess.js", "entity_name": row.name}))
    traits = db.scalars(select(JccTrait).where(JccTrait.snapshot_id == snapshot.id).order_by(JccTrait.external_id)).all()
    for row in traits:
        result.append(_document(snapshot, "trait", row.external_id, [
            ("羁绊", row.name), ("类型", row.kind), ("最大等级", row.max_level), ("激活条件", _json(row.activation_list)),
        ], {**common, "source_file": "race.js/job.js", "entity_name": row.name}))
    entities = (
        (JccEquipment, "equipment", "external_id", "name", "equipment.js", ("装备", "equipment_type", "basic_description", "description")),
        (JccAugment, "augment", "external_id", "name", "hex.js", ("强化符文", "level", "description")),
        (JccAdventure, "adventure", "external_id", "title", "adventure.js", ("奇遇", "category", "price", "description")),
        (JccGalaxy, "galaxy", "external_id", "name", "galaxy.js", ("银河", "description")),
    )
    for model, kind, id_field, name_field, source, fields in entities:
        rows = db.scalars(select(model).where(model.snapshot_id == snapshot.id).order_by(getattr(model, id_field))).all()
        for row in rows:
            values = [(fields[0], getattr(row, name_field))] + [(label, getattr(row, field, None)) for label, field in ((field, field) for field in fields[1:])]
            result.append(_document(snapshot, kind, str(getattr(row, id_field)), values, {**common, "source_file": source, "entity_name": getattr(row, name_field)}))
    return tuple(result)
