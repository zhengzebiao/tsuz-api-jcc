"""Transactional persistence and current-pointer operations for JCC snapshots."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

from sqlalchemy import Select, exists, func, select, text
from sqlalchemy.orm import Session, aliased

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


@dataclass(frozen=True)
class HeroWithTraits:
    hero: JccHero
    traits: tuple[JccTrait, ...]
    classes: tuple[JccTrait, ...]


@dataclass(frozen=True)
class TraitWithTiers:
    trait: JccTrait
    tiers: tuple[JccTraitTier, ...]


@dataclass(frozen=True)
class EquipmentWithComponents:
    equipment: JccEquipment
    components: tuple[JccEquipment, ...]


ItemT = TypeVar("ItemT")


@dataclass(frozen=True)
class SnapshotListResult(Generic[ItemT]):
    snapshot: JccSnapshot
    items: tuple[ItemT, ...]
    total: int


@dataclass(frozen=True)
class SnapshotItemResult(Generic[ItemT]):
    snapshot: JccSnapshot
    item: ItemT | None


@dataclass(frozen=True)
class ResourceCount:
    resource: str
    count: int


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


def get_current_resource_counts(db: Session, mode: str) -> tuple[JccSnapshot, tuple[ResourceCount, ...]]:
    snapshot = _required_current_snapshot(db, mode)
    models = (
        ("heroes", JccHero),
        ("traits", JccTrait),
        ("trait_tiers", JccTraitTier),
        ("hero_traits", JccHeroTrait),
        ("equipment", JccEquipment),
        ("equipment_recipes", JccEquipmentRecipe),
        ("augments", JccAugment),
        ("adventures", JccAdventure),
        ("galaxies", JccGalaxy),
    )
    counts: list[ResourceCount] = []
    for name, model in models:
        if model is JccTraitTier:
            statement = (
                select(func.count())
                .select_from(JccTraitTier)
                .join(JccTrait, JccTrait.id == JccTraitTier.trait_id)
                .where(JccTrait.snapshot_id == snapshot.id)
            )
        else:
            statement = select(func.count()).select_from(model).where(model.snapshot_id == snapshot.id)
        counts.append(ResourceCount(resource=name, count=int(db.scalar(statement) or 0)))
    return snapshot, tuple(counts)


def _required_current_snapshot(db: Session, mode: str) -> JccSnapshot:
    snapshot = _current_snapshot(db, mode)
    if snapshot is None:
        raise LookupError("no current snapshot")
    return snapshot


def _contains(column, value: str):
    return column.contains(value, autoescape=True)


def _page(db: Session, statement: Select, *, limit: int, offset: int) -> tuple[tuple[object, ...], int]:
    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    items = tuple(db.scalars(statement.limit(limit).offset(offset)).all())
    return items, total


def _stable_external_id_order(model):
    return func.length(model.external_id), model.external_id


def _load_hero_traits(db: Session, snapshot_id: int, hero_ids: list[int]) -> dict[int, tuple[list[JccTrait], list[JccTrait]]]:
    grouped = {hero_id: ([], []) for hero_id in hero_ids}
    if not hero_ids:
        return grouped
    rows = db.execute(
        select(JccHeroTrait.hero_id, JccHeroTrait.relation_kind, JccTrait)
        .join(JccTrait, JccTrait.id == JccHeroTrait.trait_id)
        .where(
            JccHeroTrait.snapshot_id == snapshot_id,
            JccTrait.snapshot_id == snapshot_id,
            JccHeroTrait.hero_id.in_(hero_ids),
        )
        .order_by(JccHeroTrait.hero_id, JccHeroTrait.position, func.length(JccTrait.external_id), JccTrait.external_id)
    ).all()
    for hero_id, relation_kind, trait in rows:
        traits, classes = grouped[hero_id]
        (traits if relation_kind == "race" else classes).append(trait)
    return grouped


def _heroes_with_traits(db: Session, snapshot_id: int, heroes: tuple[JccHero, ...]) -> tuple[HeroWithTraits, ...]:
    grouped = _load_hero_traits(db, snapshot_id, [hero.id for hero in heroes])
    return tuple(
        HeroWithTraits(hero=hero, traits=tuple(grouped[hero.id][0]), classes=tuple(grouped[hero.id][1]))
        for hero in heroes
    )


def list_current_heroes(
    db: Session,
    mode: str,
    *,
    name: str | None = None,
    trait_id: str | None = None,
    class_id: str | None = None,
    price: int | None = None,
    limit: int,
    offset: int,
) -> SnapshotListResult[HeroWithTraits]:
    snapshot = _required_current_snapshot(db, mode)
    statement = select(JccHero).where(JccHero.snapshot_id == snapshot.id)
    if name is not None:
        statement = statement.where(_contains(JccHero.name, name))
    if price is not None:
        statement = statement.where(JccHero.price == price)
    for relation_id, relation_kind in ((trait_id, "race"), (class_id, "job")):
        if relation_id is None:
            continue
        relation = aliased(JccHeroTrait)
        trait = aliased(JccTrait)
        statement = statement.where(
            exists(
                select(1)
                .select_from(relation)
                .join(
                    trait,
                    (trait.id == relation.trait_id) & (trait.snapshot_id == relation.snapshot_id),
                )
                .where(
                    relation.snapshot_id == snapshot.id,
                    relation.hero_id == JccHero.id,
                    relation.relation_kind == relation_kind,
                    trait.kind == relation_kind,
                    trait.external_id == relation_id,
                )
            )
        )
    statement = statement.order_by(*_stable_external_id_order(JccHero))
    heroes, total = _page(db, statement, limit=limit, offset=offset)
    typed_heroes = tuple(hero for hero in heroes if isinstance(hero, JccHero))
    return SnapshotListResult(snapshot, _heroes_with_traits(db, snapshot.id, typed_heroes), total)


def get_current_hero(db: Session, mode: str, hero_external_id: str) -> SnapshotItemResult[HeroWithTraits]:
    snapshot = _required_current_snapshot(db, mode)
    hero = db.scalar(
        select(JccHero).where(
            JccHero.snapshot_id == snapshot.id,
            JccHero.external_id == hero_external_id,
        )
    )
    if hero is None:
        return SnapshotItemResult(snapshot, None)
    return SnapshotItemResult(snapshot, _heroes_with_traits(db, snapshot.id, (hero,))[0])


def _load_trait_tiers(db: Session, trait_ids: list[int]) -> dict[int, list[JccTraitTier]]:
    grouped = {trait_id: [] for trait_id in trait_ids}
    if not trait_ids:
        return grouped
    tiers = db.scalars(
        select(JccTraitTier)
        .where(JccTraitTier.trait_id.in_(trait_ids))
        .order_by(JccTraitTier.trait_id, JccTraitTier.tier_order, JccTraitTier.external_id)
    ).all()
    for tier in tiers:
        grouped[tier.trait_id].append(tier)
    return grouped


def list_current_traits(
    db: Session,
    mode: str,
    *,
    kind: str | None = None,
    name: str | None = None,
    limit: int,
    offset: int,
) -> SnapshotListResult[TraitWithTiers]:
    snapshot = _required_current_snapshot(db, mode)
    statement = select(JccTrait).where(JccTrait.snapshot_id == snapshot.id)
    if kind is not None:
        statement = statement.where(JccTrait.kind == kind)
    if name is not None:
        statement = statement.where(_contains(JccTrait.name, name))
    statement = statement.order_by(*_stable_external_id_order(JccTrait), JccTrait.kind)
    traits, total = _page(db, statement, limit=limit, offset=offset)
    typed_traits = tuple(trait for trait in traits if isinstance(trait, JccTrait))
    tiers = _load_trait_tiers(db, [trait.id for trait in typed_traits])
    items = tuple(TraitWithTiers(trait, tuple(tiers[trait.id])) for trait in typed_traits)
    return SnapshotListResult(snapshot, items, total)


def _load_equipment_components(
    db: Session,
    snapshot_id: int,
    equipment_ids: list[int],
) -> dict[int, tuple[JccEquipment, ...]]:
    grouped: dict[int, tuple[JccEquipment, ...]] = {equipment_id: () for equipment_id in equipment_ids}
    if not equipment_ids:
        return grouped
    first = aliased(JccEquipment)
    second = aliased(JccEquipment)
    rows = db.execute(
        select(JccEquipmentRecipe.equipment_id, first, second)
        .join(
            first,
            (first.id == JccEquipmentRecipe.first_component_equipment_id)
            & (first.snapshot_id == JccEquipmentRecipe.snapshot_id),
        )
        .join(
            second,
            (second.id == JccEquipmentRecipe.second_component_equipment_id)
            & (second.snapshot_id == JccEquipmentRecipe.snapshot_id),
        )
        .where(
            JccEquipmentRecipe.snapshot_id == snapshot_id,
            JccEquipmentRecipe.equipment_id.in_(equipment_ids),
        )
        .order_by(JccEquipmentRecipe.equipment_id)
    ).all()
    for equipment_id, first_component, second_component in rows:
        grouped[equipment_id] = (first_component, second_component)
    return grouped


def list_current_equipment(
    db: Session,
    mode: str,
    *,
    name: str | None = None,
    equipment_type: str | None = None,
    limit: int,
    offset: int,
) -> SnapshotListResult[EquipmentWithComponents]:
    snapshot = _required_current_snapshot(db, mode)
    statement = select(JccEquipment).where(JccEquipment.snapshot_id == snapshot.id)
    if name is not None:
        statement = statement.where(_contains(JccEquipment.name, name))
    if equipment_type is not None:
        statement = statement.where(JccEquipment.equipment_type == equipment_type)
    statement = statement.order_by(*_stable_external_id_order(JccEquipment))
    equipment, total = _page(db, statement, limit=limit, offset=offset)
    typed_equipment = tuple(item for item in equipment if isinstance(item, JccEquipment))
    components = _load_equipment_components(db, snapshot.id, [item.id for item in typed_equipment])
    items = tuple(EquipmentWithComponents(item, components[item.id]) for item in typed_equipment)
    return SnapshotListResult(snapshot, items, total)


def _list_current_simple_entities(
    db: Session,
    mode: str,
    model,
    *,
    filters: tuple[object, ...],
    limit: int,
    offset: int,
) -> SnapshotListResult:
    snapshot = _required_current_snapshot(db, mode)
    statement = (
        select(model)
        .where(model.snapshot_id == snapshot.id, *filters)
        .order_by(*_stable_external_id_order(model))
    )
    items, total = _page(db, statement, limit=limit, offset=offset)
    return SnapshotListResult(snapshot, items, total)


def list_current_augments(
    db: Session,
    mode: str,
    *,
    name: str | None = None,
    level: int | None = None,
    limit: int,
    offset: int,
) -> SnapshotListResult[JccAugment]:
    filters = []
    if name is not None:
        filters.append(_contains(JccAugment.name, name))
    if level is not None:
        filters.append(JccAugment.level == level)
    return _list_current_simple_entities(
        db, mode, JccAugment, filters=tuple(filters), limit=limit, offset=offset
    )


def list_current_adventures(
    db: Session,
    mode: str,
    *,
    title: str | None = None,
    price: int | None = None,
    limit: int,
    offset: int,
) -> SnapshotListResult[JccAdventure]:
    filters = []
    if title is not None:
        filters.append(_contains(JccAdventure.title, title))
    if price is not None:
        filters.append(JccAdventure.price == price)
    return _list_current_simple_entities(
        db, mode, JccAdventure, filters=tuple(filters), limit=limit, offset=offset
    )


def list_current_galaxies(
    db: Session,
    mode: str,
    *,
    name: str | None = None,
    limit: int,
    offset: int,
) -> SnapshotListResult[JccGalaxy]:
    filters = () if name is None else (_contains(JccGalaxy.name, name),)
    return _list_current_simple_entities(
        db, mode, JccGalaxy, filters=filters, limit=limit, offset=offset
    )


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
