"""SQLAlchemy models for immutable, versioned JCC structured data."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

SnapshotId = BigInteger().with_variant(Integer, "sqlite")


def _now() -> datetime:
    return datetime.now(UTC)


class JccSnapshot(Base):
    __tablename__ = "jcc_snapshots"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_jcc_snapshots_revision_positive"),
        UniqueConstraint("mode", "season", "version", "revision", name="uq_jcc_snapshots_version_revision"),
        UniqueConstraint("mode", "season", "version", "content_hash", name="uq_jcc_snapshots_version_hash"),
        UniqueConstraint("mode", "id", name="uq_jcc_snapshots_mode_id"),
    )

    id: Mapped[int] = mapped_column(SnapshotId, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    mode_name: Mapped[str] = mapped_column(String(128), nullable=False)
    season: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    set_id: Mapped[str] = mapped_column(String(32), nullable=False)
    source_updated_at: Mapped[str | None] = mapped_column(String(64))
    version_start_time: Mapped[str | None] = mapped_column(String(64))
    raw_directory_name: Mapped[str] = mapped_column(String(160), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_manifest: Mapped[dict] = mapped_column(JSON, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class JccCurrentSnapshot(Base):
    __tablename__ = "jcc_current_snapshots"

    mode: Mapped[str] = mapped_column(String(32), primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(SnapshotId, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["mode", "snapshot_id"],
            ["jcc_snapshots.mode", "jcc_snapshots.id"],
            name="fk_jcc_current_snapshots_snapshot",
        ),
    )


class JccHero(Base):
    __tablename__ = "jcc_heroes"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "id", name="uq_jcc_heroes_snapshot_id_id"),
        UniqueConstraint("snapshot_id", "external_id", name="uq_jcc_heroes_snapshot_external_id"),
        ForeignKeyConstraint(["snapshot_id"], ["jcc_snapshots.id"], name="fk_jcc_heroes_snapshot"),
    )

    id: Mapped[int] = mapped_column(SnapshotId, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(SnapshotId, nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    price: Mapped[int | None] = mapped_column(Integer)
    hero_type: Mapped[str | None] = mapped_column(String(32))
    map_id: Mapped[int | None] = mapped_column(Integer)
    health: Mapped[int | None] = mapped_column(Integer)
    attack_damage: Mapped[int | None] = mapped_column(Integer)
    armor: Mapped[int | None] = mapped_column(Integer)
    magic_resist: Mapped[int | None] = mapped_column(Integer)
    attack_speed: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    attack_range: Mapped[int | None] = mapped_column(Integer)
    initial_mana: Mapped[int | None] = mapped_column(Integer)
    max_mana: Mapped[int | None] = mapped_column(Integer)
    skill_name: Mapped[str | None] = mapped_column(String(255))
    skill_description: Mapped[str | None] = mapped_column(String())
    skill_values: Mapped[dict | None] = mapped_column(JSON)
    image_url: Mapped[str | None] = mapped_column(String(1024))
    skill_icon_url: Mapped[str | None] = mapped_column(String(1024))
    source_attributes: Mapped[dict] = mapped_column(JSON, nullable=False)


class JccTrait(Base):
    __tablename__ = "jcc_traits"
    __table_args__ = (
        CheckConstraint("kind IN ('race', 'job')", name="ck_jcc_traits_kind"),
        UniqueConstraint("snapshot_id", "id", name="uq_jcc_traits_snapshot_id_id"),
        UniqueConstraint("snapshot_id", "kind", "external_id", name="uq_jcc_traits_snapshot_kind_external_id"),
        ForeignKeyConstraint(["snapshot_id"], ["jcc_snapshots.id"], name="fk_jcc_traits_snapshot"),
    )

    id: Mapped[int] = mapped_column(SnapshotId, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(SnapshotId, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prefix: Mapped[str | None] = mapped_column(String())
    max_level: Mapped[int | None] = mapped_column(Integer)
    activation_list: Mapped[list] = mapped_column(JSON, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String(1024))
    map_id: Mapped[int | None] = mapped_column(Integer)
    source_attributes: Mapped[dict] = mapped_column(JSON, nullable=False)


class JccTraitTier(Base):
    __tablename__ = "jcc_trait_tiers"
    __table_args__ = (
        UniqueConstraint("trait_id", "tier_order", name="uq_jcc_trait_tiers_trait_order"),
        UniqueConstraint("trait_id", "level", name="uq_jcc_trait_tiers_trait_level"),
        ForeignKeyConstraint(["trait_id"], ["jcc_traits.id"], name="fk_jcc_trait_tiers_trait"),
    )

    id: Mapped[int] = mapped_column(SnapshotId, primary_key=True, autoincrement=True)
    trait_id: Mapped[int] = mapped_column(SnapshotId, nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tier_order: Mapped[int] = mapped_column(Integer, nullable=False)
    activation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(String())
    real_description: Mapped[str | None] = mapped_column(String())
    source_attributes: Mapped[dict] = mapped_column(JSON, nullable=False)


class JccHeroTrait(Base):
    __tablename__ = "jcc_hero_traits"
    __table_args__ = (
        CheckConstraint("relation_kind IN ('race', 'job')", name="ck_jcc_hero_traits_relation_kind"),
        UniqueConstraint("snapshot_id", "hero_id", "trait_id", name="uq_jcc_hero_traits_relation"),
        ForeignKeyConstraint(
            ["snapshot_id", "hero_id"],
            ["jcc_heroes.snapshot_id", "jcc_heroes.id"],
            name="fk_jcc_hero_traits_hero",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "trait_id"],
            ["jcc_traits.snapshot_id", "jcc_traits.id"],
            name="fk_jcc_hero_traits_trait",
        ),
    )

    id: Mapped[int] = mapped_column(SnapshotId, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(SnapshotId, nullable=False, index=True)
    hero_id: Mapped[int] = mapped_column(SnapshotId, nullable=False)
    trait_id: Mapped[int] = mapped_column(SnapshotId, nullable=False)
    relation_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class JccEquipment(Base):
    __tablename__ = "jcc_equipment"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "id", name="uq_jcc_equipment_snapshot_id_id"),
        UniqueConstraint("snapshot_id", "external_id", name="uq_jcc_equipment_snapshot_external_id"),
        ForeignKeyConstraint(["snapshot_id"], ["jcc_snapshots.id"], name="fk_jcc_equipment_snapshot"),
    )

    id: Mapped[int] = mapped_column(SnapshotId, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(SnapshotId, nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    equipment_type: Mapped[str | None] = mapped_column(String(128))
    basic_description: Mapped[str | None] = mapped_column(String())
    description: Mapped[str | None] = mapped_column(String())
    image_url: Mapped[str | None] = mapped_column(String(1024))
    source_attributes: Mapped[dict] = mapped_column(JSON, nullable=False)


class JccEquipmentRecipe(Base):
    __tablename__ = "jcc_equipment_recipes"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "equipment_id", name="uq_jcc_equipment_recipes_equipment"),
        ForeignKeyConstraint(
            ["snapshot_id", "equipment_id"],
            ["jcc_equipment.snapshot_id", "jcc_equipment.id"],
            name="fk_jcc_equipment_recipes_equipment",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "first_component_equipment_id"],
            ["jcc_equipment.snapshot_id", "jcc_equipment.id"],
            name="fk_jcc_equipment_recipes_first_component",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "second_component_equipment_id"],
            ["jcc_equipment.snapshot_id", "jcc_equipment.id"],
            name="fk_jcc_equipment_recipes_second_component",
        ),
    )

    id: Mapped[int] = mapped_column(SnapshotId, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(SnapshotId, nullable=False, index=True)
    equipment_id: Mapped[int] = mapped_column(SnapshotId, nullable=False)
    first_component_equipment_id: Mapped[int] = mapped_column(SnapshotId, nullable=False)
    second_component_equipment_id: Mapped[int] = mapped_column(SnapshotId, nullable=False)


class JccAugment(Base):
    __tablename__ = "jcc_augments"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "id", name="uq_jcc_augments_snapshot_id_id"),
        UniqueConstraint("snapshot_id", "external_id", name="uq_jcc_augments_snapshot_external_id"),
        ForeignKeyConstraint(["snapshot_id"], ["jcc_snapshots.id"], name="fk_jcc_augments_snapshot"),
    )

    id: Mapped[int] = mapped_column(SnapshotId, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(SnapshotId, nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    level: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(String())
    icon_url: Mapped[str | None] = mapped_column(String(1024))
    source_attributes: Mapped[dict] = mapped_column(JSON, nullable=False)


class JccAdventure(Base):
    __tablename__ = "jcc_adventures"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "id", name="uq_jcc_adventures_snapshot_id_id"),
        UniqueConstraint("snapshot_id", "external_id", name="uq_jcc_adventures_snapshot_external_id"),
        ForeignKeyConstraint(["snapshot_id"], ["jcc_snapshots.id"], name="fk_jcc_adventures_snapshot"),
    )

    id: Mapped[int] = mapped_column(SnapshotId, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(SnapshotId, nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String())
    price: Mapped[int | None] = mapped_column(Integer)
    category: Mapped[str | None] = mapped_column(String(128))
    logo_url: Mapped[str | None] = mapped_column(String(1024))
    video_url: Mapped[str | None] = mapped_column(String(1024))
    background_image_url: Mapped[str | None] = mapped_column(String(1024))
    source_attributes: Mapped[dict] = mapped_column(JSON, nullable=False)


class JccGalaxy(Base):
    __tablename__ = "jcc_galaxies"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "id", name="uq_jcc_galaxies_snapshot_id_id"),
        UniqueConstraint("snapshot_id", "external_id", name="uq_jcc_galaxies_snapshot_external_id"),
        ForeignKeyConstraint(["snapshot_id"], ["jcc_snapshots.id"], name="fk_jcc_galaxies_snapshot"),
    )

    id: Mapped[int] = mapped_column(SnapshotId, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(SnapshotId, nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String())
    logo_url: Mapped[str | None] = mapped_column(String(1024))
    video_url: Mapped[str | None] = mapped_column(String(1024))
    background_image_url: Mapped[str | None] = mapped_column(String(1024))
    source_attributes: Mapped[dict] = mapped_column(JSON, nullable=False)


# Keep these imports discoverable for migration tools and callers that inspect the model module.
__all__ = [
    "JccAdventure",
    "JccAugment",
    "JccCurrentSnapshot",
    "JccEquipment",
    "JccEquipmentRecipe",
    "JccGalaxy",
    "JccHero",
    "JccHeroTrait",
    "JccSnapshot",
    "JccTrait",
    "JccTraitTier",
]
