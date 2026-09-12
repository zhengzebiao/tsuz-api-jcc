"""Transactional persistence and current-pointer operations for JCC snapshots."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.jcc_data.adapters import StructuredSnapshot
from app.jcc_data.models import (
    JccAdventure,
    JccAugment,
    JccCurrentSnapshot,
    JccEquipment,
    JccEquipmentRecipe,
    JccGalaxy,
    JccHero,
    JccHeroTrait,
    JccSnapshot,
    JccTrait,
    JccTraitTier,
)


@dataclass(frozen=True)
class ImportResult:
    status: str
    snapshot_id: int
    mode: str
    version: str
    revision: int
    content_hash: str


def _now() -> datetime:
    return datetime.now(UTC)


def _lock_current(db: Session, mode: str) -> JccCurrentSnapshot | None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:namespace), hashtext(:mode))"),
            {"namespace": "jcc_current_snapshots", "mode": mode},
        )
    return db.scalar(
        select(JccCurrentSnapshot).where(JccCurrentSnapshot.mode == mode).with_for_update()
    )


def _current_snapshot(db: Session, mode: str) -> JccSnapshot | None:
    current = db.scalar(select(JccCurrentSnapshot).where(JccCurrentSnapshot.mode == mode))
    if current is None:
        return None
    return db.scalar(select(JccSnapshot).where(JccSnapshot.id == current.snapshot_id))


def _expected_counts(incoming: StructuredSnapshot) -> dict[type, int]:
    return {
        JccHero: len(incoming.heroes),
        JccTrait: len(incoming.traits),
        JccHeroTrait: len(incoming.hero_traits),
        JccEquipment: len(incoming.equipment),
        JccEquipmentRecipe: len(incoming.equipment_recipes),
        JccAugment: len(incoming.augments),
        JccAdventure: len(incoming.adventures),
        JccGalaxy: len(incoming.galaxies),
    }


def _snapshot_is_complete(db: Session, snapshot_id: int, incoming: StructuredSnapshot) -> bool:
    for model, expected in _expected_counts(incoming).items():
        actual = db.scalar(select(func.count()).select_from(model).where(model.snapshot_id == snapshot_id))
        if actual != expected:
            return False
    tier_count = db.scalar(
        select(func.count())
        .select_from(JccTraitTier)
        .join(JccTrait, JccTrait.id == JccTraitTier.trait_id)
        .where(JccTrait.snapshot_id == snapshot_id)
    )
    return tier_count == sum(len(trait.tiers) for trait in incoming.traits)


def import_snapshot(db: Session, incoming: StructuredSnapshot) -> ImportResult:
    """Persist a complete structured snapshot and atomically publish it as current."""
    if db.in_transaction():
        raise RuntimeError("import_snapshot requires a session without an active transaction")
    metadata = incoming.metadata
    with db.begin():
        # PostgreSQL takes a mode-level advisory transaction lock before the
        # current row lock, including the first import when no row exists yet.
        current_pointer = _lock_current(db, metadata.mode)
        existing = db.scalar(
            select(JccSnapshot).where(
                JccSnapshot.mode == metadata.mode,
                JccSnapshot.season == metadata.season,
                JccSnapshot.version == metadata.version,
                JccSnapshot.content_hash == metadata.content_hash,
            )
        )
        if existing is not None:
            if not _snapshot_is_complete(db, existing.id, incoming):
                raise RuntimeError("existing structured snapshot is incomplete")
            if current_pointer is None:
                db.add(
                    JccCurrentSnapshot(
                        mode=metadata.mode,
                        snapshot_id=existing.id,
                        updated_at=_now(),
                    )
                )
                return ImportResult(
                    "matched", existing.id, metadata.mode, metadata.version, existing.revision, existing.content_hash
                )
            if current_pointer.snapshot_id == existing.id:
                return ImportResult(
                    "skipped", existing.id, metadata.mode, metadata.version, existing.revision, existing.content_hash
                )
            current_pointer.snapshot_id = existing.id
            current_pointer.updated_at = _now()
            return ImportResult(
                "matched", existing.id, metadata.mode, metadata.version, existing.revision, existing.content_hash
            )

        snapshot = JccSnapshot(
            mode=metadata.mode,
            mode_name=metadata.mode_name,
            season=metadata.season,
            version=metadata.version,
            revision=metadata.revision,
            set_id=metadata.set_id,
            source_updated_at=metadata.source_updated_at,
            version_start_time=(str(metadata.version_start_time) if metadata.version_start_time is not None else None),
            raw_directory_name=metadata.raw_directory_name,
            content_hash=metadata.content_hash,
            source_manifest=metadata.source_manifest,
            imported_at=_now(),
        )
        db.add(snapshot)
        db.flush()

        hero_ids: dict[str, int] = {}
        for item in incoming.heroes:
            row = JccHero(
                snapshot_id=snapshot.id,
                external_id=item.external_id,
                name=item.name,
                price=item.price,
                hero_type=item.hero_type,
                map_id=item.map_id,
                health=item.health,
                attack_damage=item.attack_damage,
                armor=item.armor,
                magic_resist=item.magic_resist,
                attack_speed=item.attack_speed,
                attack_range=item.attack_range,
                initial_mana=item.initial_mana,
                max_mana=item.max_mana,
                skill_name=item.skill_name,
                skill_description=item.skill_description,
                skill_values=item.skill_values,
                image_url=item.image_url,
                skill_icon_url=item.skill_icon_url,
                source_attributes=item.source_attributes,
            )
            db.add(row)
            db.flush()
            hero_ids[item.external_id] = row.id

        trait_ids: dict[tuple[str, str], int] = {}
        for item in incoming.traits:
            row = JccTrait(
                snapshot_id=snapshot.id,
                kind=item.kind,
                external_id=item.external_id,
                name=item.name,
                prefix=item.prefix,
                max_level=item.max_level,
                activation_list=item.activation_list,
                image_url=item.image_url,
                map_id=item.map_id,
                source_attributes=item.source_attributes,
            )
            db.add(row)
            db.flush()
            trait_ids[(item.kind, item.external_id)] = row.id
            for tier in item.tiers:
                db.add(
                    JccTraitTier(
                        trait_id=row.id,
                        external_id=tier.external_id,
                        tier_order=tier.tier_order,
                        activation_count=tier.activation_count,
                        level=tier.level,
                        description=tier.description,
                        real_description=tier.real_description,
                        source_attributes=tier.source_attributes,
                    )
                )

        db.flush()
        for relation in incoming.hero_traits:
            db.add(
                JccHeroTrait(
                    snapshot_id=snapshot.id,
                    hero_id=hero_ids[relation.hero_external_id],
                    trait_id=trait_ids[(relation.relation_kind, relation.trait_external_id)],
                    relation_kind=relation.relation_kind,
                    position=relation.position,
                )
            )

        equipment_ids: dict[str, int] = {}
        for item in incoming.equipment:
            row = JccEquipment(
                snapshot_id=snapshot.id,
                external_id=item.external_id,
                name=item.name,
                equipment_type=item.equipment_type,
                basic_description=item.basic_description,
                description=item.description,
                image_url=item.image_url,
                source_attributes=item.source_attributes,
            )
            db.add(row)
            db.flush()
            equipment_ids[item.external_id] = row.id

        db.flush()
        for recipe in incoming.equipment_recipes:
            db.add(
                JccEquipmentRecipe(
                    snapshot_id=snapshot.id,
                    equipment_id=equipment_ids[recipe.equipment_external_id],
                    first_component_equipment_id=equipment_ids[recipe.first_component_external_id],
                    second_component_equipment_id=equipment_ids[recipe.second_component_external_id],
                )
            )

        for item in incoming.augments:
            db.add(
                JccAugment(
                    snapshot_id=snapshot.id,
                    external_id=item.external_id,
                    name=item.name,
                    level=item.level,
                    description=item.description,
                    icon_url=item.icon_url,
                    source_attributes=item.source_attributes,
                )
            )
        for item in incoming.adventures:
            db.add(
                JccAdventure(
                    snapshot_id=snapshot.id,
                    external_id=item.external_id,
                    title=item.title,
                    description=item.description,
                    price=item.price,
                    category=item.category,
                    logo_url=item.logo_url,
                    video_url=item.video_url,
                    background_image_url=item.background_image_url,
                    source_attributes=item.source_attributes,
                )
            )
        for item in incoming.galaxies:
            db.add(
                JccGalaxy(
                    snapshot_id=snapshot.id,
                    external_id=item.external_id,
                    name=item.name,
                    description=item.description,
                    logo_url=item.logo_url,
                    video_url=item.video_url,
                    background_image_url=item.background_image_url,
                    source_attributes=item.source_attributes,
                )
            )

        db.flush()
        if current_pointer is None:
            db.add(JccCurrentSnapshot(mode=metadata.mode, snapshot_id=snapshot.id, updated_at=_now()))
        else:
            current_pointer.snapshot_id = snapshot.id
            current_pointer.updated_at = _now()
        return ImportResult(
            "updated", snapshot.id, metadata.mode, metadata.version, metadata.revision, metadata.content_hash
        )


def get_current_snapshot(db: Session, mode: str) -> JccSnapshot | None:
    return _current_snapshot(db, mode)


def switch_current_snapshot(db: Session, mode: str, snapshot_id: int) -> None:
    """Switch current only to a snapshot belonging to the requested mode."""
    if db.in_transaction():
        raise RuntimeError("switch_current_snapshot requires a session without an active transaction")
    with db.begin():
        pointer = _lock_current(db, mode)
        snapshot = db.scalar(
            select(JccSnapshot).where(JccSnapshot.id == snapshot_id, JccSnapshot.mode == mode)
        )
        if snapshot is None:
            raise ValueError("snapshot does not exist for mode")
        if pointer is None:
            db.add(JccCurrentSnapshot(mode=mode, snapshot_id=snapshot.id, updated_at=_now()))
        else:
            pointer.snapshot_id = snapshot.id
            pointer.updated_at = _now()


def read_snapshot_entities(db: Session, mode: str, callback: Callable[[JccSnapshot], object]) -> object:
    """Run a read callback against one stable current snapshot."""
    snapshot = _current_snapshot(db, mode)
    if snapshot is None:
        raise LookupError("no current snapshot")
    return callback(snapshot)
