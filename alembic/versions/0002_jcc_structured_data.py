"""add JCC structured data snapshots

Revision ID: 0002_jcc_structured_data
Revises: 0001_initial_app_schema
Create Date: 2026-09-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_jcc_structured_data"
down_revision: str | None = "0001_initial_app_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jcc_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("mode_name", sa.String(length=128), nullable=False),
        sa.Column("season", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("set_id", sa.String(length=32), nullable=False),
        sa.Column("source_updated_at", sa.String(length=64), nullable=True),
        sa.Column("version_start_time", sa.String(length=64), nullable=True),
        sa.Column("raw_directory_name", sa.String(length=160), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_manifest", sa.JSON(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_jcc_snapshots_revision_positive"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mode", "id", name="uq_jcc_snapshots_mode_id"),
        sa.UniqueConstraint("mode", "season", "version", "content_hash", name="uq_jcc_snapshots_version_hash"),
        sa.UniqueConstraint("mode", "season", "version", "revision", name="uq_jcc_snapshots_version_revision"),
    )
    op.create_index("ix_jcc_snapshots_mode", "jcc_snapshots", ["mode"])

    op.create_table(
        "jcc_current_snapshots",
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["mode", "snapshot_id"],
            ["jcc_snapshots.mode", "jcc_snapshots.id"],
            name="fk_jcc_current_snapshots_snapshot",
        ),
        sa.PrimaryKeyConstraint("mode"),
    )

    op.create_table(
        "jcc_heroes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("price", sa.Integer(), nullable=True),
        sa.Column("hero_type", sa.String(length=32), nullable=True),
        sa.Column("map_id", sa.Integer(), nullable=True),
        sa.Column("health", sa.Integer(), nullable=True),
        sa.Column("attack_damage", sa.Integer(), nullable=True),
        sa.Column("armor", sa.Integer(), nullable=True),
        sa.Column("magic_resist", sa.Integer(), nullable=True),
        sa.Column("attack_speed", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("attack_range", sa.Integer(), nullable=True),
        sa.Column("initial_mana", sa.Integer(), nullable=True),
        sa.Column("max_mana", sa.Integer(), nullable=True),
        sa.Column("skill_name", sa.String(length=255), nullable=True),
        sa.Column("skill_description", sa.String(), nullable=True),
        sa.Column("skill_values", sa.JSON(), nullable=True),
        sa.Column("image_url", sa.String(length=1024), nullable=True),
        sa.Column("skill_icon_url", sa.String(length=1024), nullable=True),
        sa.Column("source_attributes", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["jcc_snapshots.id"], name="fk_jcc_heroes_snapshot"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "external_id", name="uq_jcc_heroes_snapshot_external_id"),
        sa.UniqueConstraint("snapshot_id", "id", name="uq_jcc_heroes_snapshot_id_id"),
    )
    op.create_index("ix_jcc_heroes_snapshot_id", "jcc_heroes", ["snapshot_id"])

    op.create_table(
        "jcc_traits",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.CheckConstraint("kind IN ('race', 'job')", name="ck_jcc_traits_kind"),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("prefix", sa.String(), nullable=True),
        sa.Column("max_level", sa.Integer(), nullable=True),
        sa.Column("activation_list", sa.JSON(), nullable=False),
        sa.Column("image_url", sa.String(length=1024), nullable=True),
        sa.Column("map_id", sa.Integer(), nullable=True),
        sa.Column("source_attributes", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["jcc_snapshots.id"], name="fk_jcc_traits_snapshot"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "kind", "external_id", name="uq_jcc_traits_snapshot_kind_external_id"),
        sa.UniqueConstraint("snapshot_id", "id", name="uq_jcc_traits_snapshot_id_id"),
    )
    op.create_index("ix_jcc_traits_snapshot_id", "jcc_traits", ["snapshot_id"])

    op.create_table(
        "jcc_equipment",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("equipment_type", sa.String(length=128), nullable=True),
        sa.Column("basic_description", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("image_url", sa.String(length=1024), nullable=True),
        sa.Column("source_attributes", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["jcc_snapshots.id"], name="fk_jcc_equipment_snapshot"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "external_id", name="uq_jcc_equipment_snapshot_external_id"),
        sa.UniqueConstraint("snapshot_id", "id", name="uq_jcc_equipment_snapshot_id_id"),
    )
    op.create_index("ix_jcc_equipment_snapshot_id", "jcc_equipment", ["snapshot_id"])

    _create_simple_entity_tables()

    op.create_table(
        "jcc_trait_tiers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trait_id", sa.BigInteger(), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("tier_order", sa.Integer(), nullable=False),
        sa.Column("activation_count", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("real_description", sa.String(), nullable=True),
        sa.Column("source_attributes", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["trait_id"], ["jcc_traits.id"], name="fk_jcc_trait_tiers_trait"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trait_id", "tier_order", name="uq_jcc_trait_tiers_trait_order"),
        sa.UniqueConstraint("trait_id", "level", name="uq_jcc_trait_tiers_trait_level"),
    )
    op.create_index("ix_jcc_trait_tiers_trait_id", "jcc_trait_tiers", ["trait_id"])

    op.create_table(
        "jcc_hero_traits",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("hero_id", sa.BigInteger(), nullable=False),
        sa.Column("trait_id", sa.BigInteger(), nullable=False),
        sa.Column("relation_kind", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint("relation_kind IN ('race', 'job')", name="ck_jcc_hero_traits_relation_kind"),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "hero_id"],
            ["jcc_heroes.snapshot_id", "jcc_heroes.id"],
            name="fk_jcc_hero_traits_hero",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "trait_id"],
            ["jcc_traits.snapshot_id", "jcc_traits.id"],
            name="fk_jcc_hero_traits_trait",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "hero_id", "trait_id", name="uq_jcc_hero_traits_relation"),
    )
    op.create_index("ix_jcc_hero_traits_snapshot_id", "jcc_hero_traits", ["snapshot_id"])

    op.create_table(
        "jcc_equipment_recipes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("equipment_id", sa.BigInteger(), nullable=False),
        sa.Column("first_component_equipment_id", sa.BigInteger(), nullable=False),
        sa.Column("second_component_equipment_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "equipment_id"],
            ["jcc_equipment.snapshot_id", "jcc_equipment.id"],
            name="fk_jcc_equipment_recipes_equipment",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "first_component_equipment_id"],
            ["jcc_equipment.snapshot_id", "jcc_equipment.id"],
            name="fk_jcc_equipment_recipes_first_component",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "second_component_equipment_id"],
            ["jcc_equipment.snapshot_id", "jcc_equipment.id"],
            name="fk_jcc_equipment_recipes_second_component",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "equipment_id", name="uq_jcc_equipment_recipes_equipment"),
    )
    op.create_index("ix_jcc_equipment_recipes_snapshot_id", "jcc_equipment_recipes", ["snapshot_id"])


def _create_simple_entity_tables() -> None:
    op.create_table(
        "jcc_augments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("icon_url", sa.String(length=1024), nullable=True),
        sa.Column("source_attributes", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["jcc_snapshots.id"], name="fk_jcc_augments_snapshot"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "external_id", name="uq_jcc_augments_snapshot_external_id"),
        sa.UniqueConstraint("snapshot_id", "id", name="uq_jcc_augments_snapshot_id_id"),
    )
    op.create_index("ix_jcc_augments_snapshot_id", "jcc_augments", ["snapshot_id"])

    op.create_table(
        "jcc_adventures",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("price", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("logo_url", sa.String(length=1024), nullable=True),
        sa.Column("video_url", sa.String(length=1024), nullable=True),
        sa.Column("background_image_url", sa.String(length=1024), nullable=True),
        sa.Column("source_attributes", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["jcc_snapshots.id"], name="fk_jcc_adventures_snapshot"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "external_id", name="uq_jcc_adventures_snapshot_external_id"),
        sa.UniqueConstraint("snapshot_id", "id", name="uq_jcc_adventures_snapshot_id_id"),
    )
    op.create_index("ix_jcc_adventures_snapshot_id", "jcc_adventures", ["snapshot_id"])

    op.create_table(
        "jcc_galaxies",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("logo_url", sa.String(length=1024), nullable=True),
        sa.Column("video_url", sa.String(length=1024), nullable=True),
        sa.Column("background_image_url", sa.String(length=1024), nullable=True),
        sa.Column("source_attributes", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["jcc_snapshots.id"], name="fk_jcc_galaxies_snapshot"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "external_id", name="uq_jcc_galaxies_snapshot_external_id"),
        sa.UniqueConstraint("snapshot_id", "id", name="uq_jcc_galaxies_snapshot_id_id"),
    )
    op.create_index("ix_jcc_galaxies_snapshot_id", "jcc_galaxies", ["snapshot_id"])


def downgrade() -> None:
    op.drop_index("ix_jcc_equipment_recipes_snapshot_id", table_name="jcc_equipment_recipes")
    op.drop_table("jcc_equipment_recipes")
    op.drop_index("ix_jcc_hero_traits_snapshot_id", table_name="jcc_hero_traits")
    op.drop_table("jcc_hero_traits")
    op.drop_index("ix_jcc_trait_tiers_trait_id", table_name="jcc_trait_tiers")
    op.drop_table("jcc_trait_tiers")
    for table in ("jcc_galaxies", "jcc_adventures", "jcc_augments"):
        op.drop_index(f"ix_{table}_snapshot_id", table_name=table)
        op.drop_table(table)
    op.drop_index("ix_jcc_equipment_snapshot_id", table_name="jcc_equipment")
    op.drop_table("jcc_equipment")
    op.drop_index("ix_jcc_traits_snapshot_id", table_name="jcc_traits")
    op.drop_table("jcc_traits")
    op.drop_index("ix_jcc_heroes_snapshot_id", table_name="jcc_heroes")
    op.drop_table("jcc_heroes")
    op.drop_table("jcc_current_snapshots")
    op.drop_index("ix_jcc_snapshots_mode", table_name="jcc_snapshots")
    op.drop_table("jcc_snapshots")
