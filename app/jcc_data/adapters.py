"""Convert validated JCC raw data into a database-neutral snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.jcc_data.snapshot_reader import RawSnapshot
from app.jcc_data.validation import (
    DataValidationError,
    decimal,
    ensure_key_matches_id,
    field_string,
    integer,
    optional_string,
    required_id,
    required_string,
    split_ids,
    split_ints,
)


@dataclass(frozen=True)
class SnapshotData:
    mode: str
    mode_name: str
    season: str
    version: str
    revision: int
    set_id: str
    raw_directory_name: str
    content_hash: str
    source_updated_at: str | None
    version_start_time: Any
    source_manifest: dict[str, Any]


@dataclass(frozen=True)
class HeroData:
    external_id: str
    name: str
    price: int | None
    hero_type: str | None
    map_id: int | None
    health: int | None
    attack_damage: int | None
    armor: int | None
    magic_resist: int | None
    attack_speed: Decimal | None
    attack_range: int | None
    initial_mana: int | None
    max_mana: int | None
    skill_name: str | None
    skill_description: str | None
    skill_values: dict[str, str] | None
    image_url: str | None
    skill_icon_url: str | None
    source_attributes: dict[str, Any]


@dataclass(frozen=True)
class TraitTierData:
    external_id: str
    tier_order: int
    activation_count: int
    level: int
    description: str | None
    real_description: str | None
    source_attributes: dict[str, Any]


@dataclass(frozen=True)
class TraitData:
    kind: str
    external_id: str
    name: str
    prefix: str | None
    max_level: int | None
    activation_list: list[int]
    image_url: str | None
    map_id: int | None
    source_attributes: dict[str, Any]
    tiers: list[TraitTierData] = field(default_factory=list)


@dataclass(frozen=True)
class HeroTraitData:
    hero_external_id: str
    trait_external_id: str
    relation_kind: str
    position: int


@dataclass(frozen=True)
class EquipmentData:
    external_id: str
    name: str
    equipment_type: str | None
    basic_description: str | None
    description: str | None
    image_url: str | None
    source_attributes: dict[str, Any]


@dataclass(frozen=True)
class EquipmentRecipeData:
    equipment_external_id: str
    first_component_external_id: str
    second_component_external_id: str


@dataclass(frozen=True)
class AugmentData:
    external_id: str
    name: str
    level: int | None
    description: str | None
    icon_url: str | None
    source_attributes: dict[str, Any]


@dataclass(frozen=True)
class AdventureData:
    external_id: str
    title: str
    description: str | None
    price: int | None
    category: str | None
    logo_url: str | None
    video_url: str | None
    background_image_url: str | None
    source_attributes: dict[str, Any]


@dataclass(frozen=True)
class GalaxyData:
    external_id: str
    name: str
    description: str | None
    logo_url: str | None
    video_url: str | None
    background_image_url: str | None
    source_attributes: dict[str, Any]


@dataclass(frozen=True)
class StructuredSnapshot:
    metadata: SnapshotData
    heroes: list[HeroData]
    traits: list[TraitData]
    hero_traits: list[HeroTraitData]
    equipment: list[EquipmentData]
    equipment_recipes: list[EquipmentRecipeData]
    augments: list[AugmentData]
    adventures: list[AdventureData]
    galaxies: list[GalaxyData]


def _data(raw: RawSnapshot, name: str) -> dict[str, dict[str, Any]]:
    value = raw.payloads[name].get("data")
    if not isinstance(value, dict):
        raise DataValidationError(f"{name}: data must be object")
    return value


def _source(record: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key in allowed}


def _skill_values(record: dict[str, Any], context: str) -> dict[str, str] | None:
    values = {}
    for key in ("skillBriefValue", "skillValueDesc"):
        value = optional_string(record, key, context)
        if value is not None:
            values[key] = value
    return values or None


def _build_traits(raw: RawSnapshot) -> tuple[list[TraitData], dict[tuple[str, str], TraitData]]:
    result: dict[tuple[str, str], TraitData] = {}
    race = _data(raw, "race")
    job = _data(raw, "job")
    for kind, records in (("race", race), ("job", job)):
        for key, record in records.items():
            if not isinstance(record, dict):
                raise DataValidationError(f"{kind} {key}: expected object")
            external_id = ensure_key_matches_id(key, record, "id", f"{kind} {key}")
            activation = split_ints(record.get("numList"), f"{kind} {external_id} numList")
            max_level = integer(record.get("maxLevel"), f"{kind} {external_id} maxLevel", required=False)
            if max_level is not None and max_level != len(activation):
                raise DataValidationError(f"{kind} {external_id}: maxLevel does not match numList")
            result[(kind, external_id)] = TraitData(
                kind=kind,
                external_id=external_id,
                name=required_string(record, "name", f"{kind} {external_id}"),
                prefix=optional_string(record, "prefix", f"{kind} {external_id}"),
                max_level=max_level,
                activation_list=activation,
                image_url=field_string(record, ("picture",), f"{kind} {external_id}"),
                map_id=integer(record.get("mapID"), f"{kind} {external_id} mapID", required=False),
                source_attributes=_source(record, {"color", "desc2", "tftSpecId", "tftClassId"}),
            )

    tiers: dict[tuple[str, str], dict[int, TraitTierData]] = {}
    trait_records = _data(raw, "trait")
    for key, record in trait_records.items():
        if not isinstance(record, dict):
            raise DataValidationError(f"trait {key}: expected object")
        external_id = ensure_key_matches_id(key, record, "id", f"trait {key}")
        check_id = required_string(record, "checkId", f"trait {external_id}")
        type_value = integer(record.get("type"), f"trait {external_id} type")
        kind = {0: "race", 1: "job"}.get(type_value)
        if kind is None or (kind, check_id) not in result:
            raise DataValidationError(f"trait {external_id}: unknown base trait")
        level = integer(record.get("level"), f"trait {external_id} level")
        activation_count = integer(record.get("num"), f"trait {external_id} num")
        tier = TraitTierData(
            external_id=external_id,
            tier_order=level,
            activation_count=activation_count,
            level=level,
            description=optional_string(record, "desc", f"trait {external_id}"),
            real_description=optional_string(record, "realDesc", f"trait {external_id}"),
            source_attributes=_source(record, {"color", "desc2", "values", "prefix"}),
        )
        by_level = tiers.setdefault((kind, check_id), {})
        if level in by_level:
            raise DataValidationError(f"trait {check_id}: duplicate level {level}")
        by_level[level] = tier

    finalized: list[TraitData] = []
    for key, trait in result.items():
        trait_tiers = tiers.get(key, {})
        if not trait_tiers:
            raise DataValidationError(f"{trait.kind} {trait.external_id}: missing trait tiers")
        expected_levels = list(range(1, len(trait_tiers) + 1))
        if sorted(trait_tiers) != expected_levels:
            raise DataValidationError(f"{trait.kind} {trait.external_id}: non-contiguous trait levels")
        if trait.max_level is not None and len(trait_tiers) != trait.max_level:
            raise DataValidationError(f"{trait.kind} {trait.external_id}: tier count does not match maxLevel")
        if [trait_tiers[level].activation_count for level in expected_levels] != trait.activation_list:
            raise DataValidationError(f"{trait.kind} {trait.external_id}: tiers do not match numList")
        finalized.append(
            TraitData(
                kind=trait.kind,
                external_id=trait.external_id,
                name=trait.name,
                prefix=trait.prefix,
                max_level=trait.max_level,
                activation_list=trait.activation_list,
                image_url=trait.image_url,
                map_id=trait.map_id,
                source_attributes=trait.source_attributes,
                tiers=[trait_tiers[level] for level in sorted(trait_tiers)],
            )
        )
    return finalized, result


def _build_heroes(raw: RawSnapshot, traits: dict[tuple[str, str], TraitData]) -> tuple[list[HeroData], list[HeroTraitData]]:
    result: list[HeroData] = []
    relations: list[HeroTraitData] = []
    for key, record in _data(raw, "chess").items():
        if not isinstance(record, dict):
            raise DataValidationError(f"hero {key}: expected object")
        external_id = ensure_key_matches_id(key, record, "id", f"hero {key}")
        hero = HeroData(
            external_id=external_id,
            name=required_string(record, "name", f"hero {external_id}"),
            price=integer(record.get("price"), f"hero {external_id} price", required=False),
            hero_type=optional_string(record, "heroType", f"hero {external_id}"),
            map_id=integer(record.get("mapID"), f"hero {external_id} mapID", required=False),
            health=integer(record.get("initHP"), f"hero {external_id} initHP", required=False),
            attack_damage=integer(record.get("initAttackDamage"), f"hero {external_id} attackDamage", required=False),
            armor=integer(record.get("armor"), f"hero {external_id} armor", required=False),
            magic_resist=integer(record.get("magicResist"), f"hero {external_id} magicResist", required=False),
            attack_speed=decimal(record.get("attackSpeed"), f"hero {external_id} attackSpeed", required=False),
            attack_range=integer(record.get("attackRange"), f"hero {external_id} attackRange", required=False),
            initial_mana=integer(record.get("initMP"), f"hero {external_id} initMP", required=False),
            max_mana=integer(record.get("maxMP"), f"hero {external_id} maxMP", required=False),
            skill_name=optional_string(record, "skillName", f"hero {external_id}"),
            skill_description=optional_string(record, "skillDesc", f"hero {external_id}"),
            skill_values=_skill_values(record, f"hero {external_id}"),
            image_url=optional_string(record, "picture", f"hero {external_id}"),
            skill_icon_url=optional_string(record, "skillIcon", f"hero {external_id}"),
            source_attributes=_source(
                record,
                {
                    "heroPaint",
                    "criticalStrikeChance",
                    "buyPrice",
                    "sellPrice",
                    "setid",
                    "tftHeroId",
                    "showHeroTag",
                },
            ),
        )
        result.append(hero)
        for relation_kind, relation_field in (("job", "class"), ("race", "species")):
            relation_ids = split_ids(record.get(relation_field), f"hero {external_id} {relation_field}")
            for position, trait_id in enumerate(relation_ids):
                if (relation_kind, trait_id) not in traits:
                    raise DataValidationError(f"hero {external_id}: unknown {relation_kind} {trait_id}")
                relations.append(HeroTraitData(external_id, trait_id, relation_kind, position))
    return result, relations


def _build_equipment(raw: RawSnapshot) -> tuple[list[EquipmentData], list[EquipmentRecipeData]]:
    records = _data(raw, "equip")
    result: list[EquipmentData] = []
    recipes: list[EquipmentRecipeData] = []
    for key, record in records.items():
        if not isinstance(record, dict):
            raise DataValidationError(f"equipment {key}: expected object")
        external_id = ensure_key_matches_id(key, record, "id", f"equipment {key}")
        result.append(
            EquipmentData(
                external_id=external_id,
                name=required_string(record, "name", f"equipment {external_id}"),
                equipment_type=optional_string(record, "type", f"equipment {external_id}"),
                basic_description=optional_string(record, "basicDesc", f"equipment {external_id}"),
                description=optional_string(record, "desc", f"equipment {external_id}"),
                image_url=optional_string(record, "picture", f"equipment {external_id}"),
                source_attributes=_source(record, {"EffectType", "fetterID", "icon", "mapID", "planID", "setID", "sort", "tftEquipId"}),
            )
        )
    ids = {item.external_id for item in result}
    for key, record in records.items():
        first = optional_string(record, "synthesis1", f"equipment {key}")
        second = optional_string(record, "synthesis2", f"equipment {key}")
        components = [component for component in (first, second) if component and component != "0"]
        if not components:
            continue
        if len(components) != 2 or any(component not in ids for component in components):
            raise DataValidationError(f"equipment {key}: invalid recipe")
        recipes.append(EquipmentRecipeData(str(key), components[0], components[1]))
    return result, recipes


def _build_augments(raw: RawSnapshot) -> list[AugmentData]:
    result = []
    for key, record in _data(raw, "hex").items():
        if not isinstance(record, dict):
            raise DataValidationError(f"augment {key}: expected object")
        external_id = ensure_key_matches_id(key, record, "id", f"augment {key}")
        result.append(
            AugmentData(
                external_id=external_id,
                name=required_string(record, "name", f"augment {external_id}"),
                level=integer(record.get("level"), f"augment {external_id} level", required=False),
                description=optional_string(record, "desc", f"augment {external_id}"),
                icon_url=optional_string(record, "icon", f"augment {external_id}"),
                source_attributes=_source(record, {"is_legend", "hero_enhancement_type", "fetterId", "fetterType"}),
            )
        )
    return result


def _build_adventures(raw: RawSnapshot) -> list[AdventureData]:
    result = []
    for key, record in _data(raw, "adventure").items():
        if not isinstance(record, dict):
            raise DataValidationError(f"adventure {key}: expected object")
        external_id = required_id(record, "adventureId", f"adventure {key}")
        result.append(
            AdventureData(
                external_id=external_id,
                title=required_string(record, "title", f"adventure {external_id}"),
                description=optional_string(record, "desc", f"adventure {external_id}"),
                price=integer(record.get("price"), f"adventure {external_id} price", required=False),
                category=optional_string(record, "category", f"adventure {external_id}"),
                logo_url=optional_string(record, "logo", f"adventure {external_id}"),
                video_url=optional_string(record, "video", f"adventure {external_id}"),
                background_image_url=optional_string(record, "bgImage", f"adventure {external_id}"),
                source_attributes=_source(record, {"remark"}),
            )
        )
    return result


def _build_galaxies(raw: RawSnapshot) -> list[GalaxyData]:
    result = []
    for key, record in _data(raw, "galaxy").items():
        if not isinstance(record, dict):
            raise DataValidationError(f"galaxy {key}: expected object")
        external_id = ensure_key_matches_id(key, record, "id", f"galaxy {key}")
        result.append(
            GalaxyData(
                external_id=external_id,
                name=required_string(record, "name", f"galaxy {external_id}"),
                description=optional_string(record, "desc", f"galaxy {external_id}"),
                logo_url=optional_string(record, "logo", f"galaxy {external_id}"),
                video_url=optional_string(record, "video", f"galaxy {external_id}"),
                background_image_url=optional_string(record, "bgImage", f"galaxy {external_id}"),
                source_attributes=_source(record, {"country"}),
            )
        )
    return result


def build_structured_snapshot(raw: RawSnapshot) -> StructuredSnapshot:
    traits, trait_index = _build_traits(raw)
    heroes, hero_traits = _build_heroes(raw, trait_index)
    equipment, recipes = _build_equipment(raw)
    chess = raw.payloads["chess"]
    metadata = SnapshotData(
        mode=raw.spec.mode,
        mode_name=raw.spec.mode_name,
        season=raw.spec.season,
        version=raw.spec.version,
        revision=raw.revision,
        set_id=raw.spec.mode,
        raw_directory_name=raw.directory.name,
        content_hash=raw.content_hash,
        source_updated_at=chess.get("time") if isinstance(chess, dict) else None,
        version_start_time=raw.spec.version_start_time,
        source_manifest=raw.manifest,
    )
    return StructuredSnapshot(
        metadata=metadata,
        heroes=heroes,
        traits=traits,
        hero_traits=hero_traits,
        equipment=equipment,
        equipment_recipes=recipes,
        augments=_build_augments(raw),
        adventures=_build_adventures(raw),
        galaxies=_build_galaxies(raw),
    )
