import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import AfterValidator, StringConstraints
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DbSession

from app.core.config import settings
from app.core.database import get_db
from app.deps.auth import require_scope
from app.jcc_data.models import JccSnapshot
from app.jcc_data.repository import (
    EquipmentWithComponents,
    HeroWithTraits,
    SnapshotListResult,
    TraitWithTiers,
    get_current_hero,
    get_current_snapshot,
    list_current_adventures,
    list_current_augments,
    list_current_equipment,
    list_current_galaxies,
    list_current_heroes,
    list_current_traits,
)
from app.schemas.jcc import (
    JccAdventureItem,
    JccAugmentItem,
    JccDataResponse,
    JccEquipmentComponent,
    JccEquipmentItem,
    JccGalaxyItem,
    JccHeroItem,
    JccListResponse,
    JccSnapshotData,
    JccSnapshotMetadata,
    JccTraitItem,
    JccTraitReference,
    JccTraitTierItem,
)

logger = logging.getLogger("app.jcc")
_DB_DEPENDENCY = Depends(get_db)
_READ_DEPENDENCY = Depends(require_scope("jcc:data:read"))

router = APIRouter(
    prefix="/jcc",
    tags=["jcc"],
    dependencies=[_READ_DEPENDENCY],
    responses={
        401: {"description": "Invalid user token"},
        403: {"description": "Insufficient user scope"},
        503: {"description": "JCC data unavailable"},
    },
)


def _not_blank(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


SearchText = Annotated[
    str | None,
    StringConstraints(min_length=1, max_length=255),
    AfterValidator(_not_blank),
]
ExternalId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, strip_whitespace=True, pattern=r"^[A-Za-z0-9._:-]+$"),
]
OptionalExternalId = Annotated[
    str | None,
    StringConstraints(min_length=1, max_length=64, strip_whitespace=True, pattern=r"^[A-Za-z0-9._:-]+$"),
]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


def _snapshot_metadata(snapshot: JccSnapshot) -> JccSnapshotMetadata:
    return JccSnapshotMetadata(
        mode=snapshot.mode,
        mode_name=snapshot.mode_name,
        season=snapshot.season,
        version=snapshot.version,
        revision=snapshot.revision,
        content_hash=snapshot.content_hash,
        source_updated_at=snapshot.source_updated_at,
    )


def _record_context(
    request: Request,
    snapshot: JccSnapshot,
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> None:
    request.state.jcc_context = {
        "snapshot_version": snapshot.version,
        "snapshot_revision": snapshot.revision,
        "limit": limit,
        "offset": offset,
    }


def _source_count(snapshot: JccSnapshot) -> int:
    sources = snapshot.source_manifest.get("sources") if isinstance(snapshot.source_manifest, dict) else None
    return len(sources) if isinstance(sources, dict) else 0


def _trait_reference(trait) -> JccTraitReference:
    return JccTraitReference(id=trait.external_id, name=trait.name, kind=trait.kind)


def _hero_item(item: HeroWithTraits) -> JccHeroItem:
    hero = item.hero
    return JccHeroItem(
        id=hero.external_id,
        name=hero.name,
        price=hero.price,
        hero_type=hero.hero_type,
        map_id=hero.map_id,
        health=hero.health,
        attack_damage=hero.attack_damage,
        armor=hero.armor,
        magic_resist=hero.magic_resist,
        attack_speed=float(hero.attack_speed) if hero.attack_speed is not None else None,
        attack_range=hero.attack_range,
        initial_mana=hero.initial_mana,
        max_mana=hero.max_mana,
        skill_name=hero.skill_name,
        skill_description=hero.skill_description,
        skill_values=hero.skill_values,
        image_url=hero.image_url,
        skill_icon_url=hero.skill_icon_url,
        traits=[_trait_reference(trait) for trait in item.traits],
        classes=[_trait_reference(trait) for trait in item.classes],
    )


def _trait_item(item: TraitWithTiers) -> JccTraitItem:
    trait = item.trait
    return JccTraitItem(
        id=trait.external_id,
        kind=trait.kind,
        name=trait.name,
        prefix=trait.prefix,
        max_level=trait.max_level,
        activation_list=trait.activation_list,
        image_url=trait.image_url,
        map_id=trait.map_id,
        tiers=[
            JccTraitTierItem(
                id=tier.external_id,
                tier_order=tier.tier_order,
                activation_count=tier.activation_count,
                level=tier.level,
                description=tier.description,
                real_description=tier.real_description,
            )
            for tier in item.tiers
        ],
    )


def _equipment_item(item: EquipmentWithComponents) -> JccEquipmentItem:
    equipment = item.equipment
    return JccEquipmentItem(
        id=equipment.external_id,
        name=equipment.name,
        type=equipment.equipment_type,
        basic_description=equipment.basic_description,
        description=equipment.description,
        image_url=equipment.image_url,
        components=[
            JccEquipmentComponent(id=component.external_id, name=component.name)
            for component in item.components
        ],
    )


def _list_response(result: SnapshotListResult, items: list, limit: int, offset: int) -> JccListResponse:
    return JccListResponse(
        snapshot=_snapshot_metadata(result.snapshot),
        items=items,
        total=result.total,
        limit=limit,
        offset=offset,
    )


def _data_unavailable(db: DbSession, exc: Exception) -> HTTPException:
    try:
        db.rollback()
    except SQLAlchemyError:
        pass
    logger.error("jcc data unavailable error_type=%s", type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="JCC_DATA_UNAVAILABLE",
    )


@router.get(
    "/snapshot",
    response_model=JccDataResponse[JccSnapshotData],
)
def current_snapshot(
    request: Request,
    db: DbSession = _DB_DEPENDENCY,
) -> JccDataResponse[JccSnapshotData]:
    try:
        snapshot = get_current_snapshot(db, settings.jcc_data_mode)
        if snapshot is None:
            raise LookupError("no current snapshot")
        _record_context(request, snapshot)
        return JccDataResponse(
            snapshot=_snapshot_metadata(snapshot),
            data=JccSnapshotData(source_count=_source_count(snapshot)),
        )
    except (LookupError, SQLAlchemyError) as exc:
        raise _data_unavailable(db, exc) from exc


@router.get("/heroes", response_model=JccListResponse[JccHeroItem])
def heroes(
    request: Request,
    name: Annotated[SearchText, Query()] = None,
    trait_id: Annotated[OptionalExternalId, Query()] = None,
    class_id: Annotated[OptionalExternalId, Query()] = None,
    price: Annotated[int | None, Query(ge=0)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
    db: DbSession = _DB_DEPENDENCY,
) -> JccListResponse[JccHeroItem]:
    try:
        result = list_current_heroes(
            db,
            settings.jcc_data_mode,
            name=name,
            trait_id=trait_id,
            class_id=class_id,
            price=price,
            limit=limit,
            offset=offset,
        )
        _record_context(request, result.snapshot, limit=limit, offset=offset)
        return _list_response(result, [_hero_item(item) for item in result.items], limit, offset)
    except (LookupError, SQLAlchemyError) as exc:
        raise _data_unavailable(db, exc) from exc


@router.get(
    "/heroes/{hero_id}",
    response_model=JccDataResponse[JccHeroItem],
    responses={404: {"description": "Hero not found"}},
)
def hero_detail(
    request: Request,
    hero_id: Annotated[ExternalId, Path()],
    db: DbSession = _DB_DEPENDENCY,
) -> JccDataResponse[JccHeroItem]:
    try:
        result = get_current_hero(db, settings.jcc_data_mode, hero_id)
        _record_context(request, result.snapshot)
        if result.item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="JCC_HERO_NOT_FOUND",
            )
        return JccDataResponse(snapshot=_snapshot_metadata(result.snapshot), data=_hero_item(result.item))
    except HTTPException:
        raise
    except (LookupError, SQLAlchemyError) as exc:
        raise _data_unavailable(db, exc) from exc


@router.get("/traits", response_model=JccListResponse[JccTraitItem])
def traits(
    request: Request,
    kind: Annotated[Literal["race", "job"] | None, Query()] = None,
    name: Annotated[SearchText, Query()] = None,
    limit: Limit = 50,
    offset: Offset = 0,
    db: DbSession = _DB_DEPENDENCY,
) -> JccListResponse[JccTraitItem]:
    try:
        result = list_current_traits(
            db, settings.jcc_data_mode, kind=kind, name=name, limit=limit, offset=offset
        )
        _record_context(request, result.snapshot, limit=limit, offset=offset)
        return _list_response(result, [_trait_item(item) for item in result.items], limit, offset)
    except (LookupError, SQLAlchemyError) as exc:
        raise _data_unavailable(db, exc) from exc


@router.get("/equipment", response_model=JccListResponse[JccEquipmentItem])
def equipment(
    request: Request,
    name: Annotated[SearchText, Query()] = None,
    equipment_type: Annotated[SearchText, Query(alias="type")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
    db: DbSession = _DB_DEPENDENCY,
) -> JccListResponse[JccEquipmentItem]:
    try:
        result = list_current_equipment(
            db,
            settings.jcc_data_mode,
            name=name,
            equipment_type=equipment_type,
            limit=limit,
            offset=offset,
        )
        _record_context(request, result.snapshot, limit=limit, offset=offset)
        return _list_response(result, [_equipment_item(item) for item in result.items], limit, offset)
    except (LookupError, SQLAlchemyError) as exc:
        raise _data_unavailable(db, exc) from exc


@router.get("/augments", response_model=JccListResponse[JccAugmentItem])
def augments(
    request: Request,
    name: Annotated[SearchText, Query()] = None,
    level: Annotated[int | None, Query(ge=0)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
    db: DbSession = _DB_DEPENDENCY,
) -> JccListResponse[JccAugmentItem]:
    try:
        result = list_current_augments(
            db, settings.jcc_data_mode, name=name, level=level, limit=limit, offset=offset
        )
        _record_context(request, result.snapshot, limit=limit, offset=offset)
        items = [
            JccAugmentItem(
                id=item.external_id,
                name=item.name,
                level=item.level,
                description=item.description,
                icon_url=item.icon_url,
            )
            for item in result.items
        ]
        return _list_response(result, items, limit, offset)
    except (LookupError, SQLAlchemyError) as exc:
        raise _data_unavailable(db, exc) from exc


@router.get("/adventures", response_model=JccListResponse[JccAdventureItem])
def adventures(
    request: Request,
    title: Annotated[SearchText, Query()] = None,
    price: Annotated[int | None, Query(ge=0)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
    db: DbSession = _DB_DEPENDENCY,
) -> JccListResponse[JccAdventureItem]:
    try:
        result = list_current_adventures(
            db, settings.jcc_data_mode, title=title, price=price, limit=limit, offset=offset
        )
        _record_context(request, result.snapshot, limit=limit, offset=offset)
        items = [
            JccAdventureItem(
                id=item.external_id,
                title=item.title,
                description=item.description,
                price=item.price,
                category=item.category,
                logo_url=item.logo_url,
                video_url=item.video_url,
                background_image_url=item.background_image_url,
            )
            for item in result.items
        ]
        return _list_response(result, items, limit, offset)
    except (LookupError, SQLAlchemyError) as exc:
        raise _data_unavailable(db, exc) from exc


@router.get("/galaxies", response_model=JccListResponse[JccGalaxyItem])
def galaxies(
    request: Request,
    name: Annotated[SearchText, Query()] = None,
    limit: Limit = 50,
    offset: Offset = 0,
    db: DbSession = _DB_DEPENDENCY,
) -> JccListResponse[JccGalaxyItem]:
    try:
        result = list_current_galaxies(
            db, settings.jcc_data_mode, name=name, limit=limit, offset=offset
        )
        _record_context(request, result.snapshot, limit=limit, offset=offset)
        items = [
            JccGalaxyItem(
                id=item.external_id,
                name=item.name,
                description=item.description,
                logo_url=item.logo_url,
                video_url=item.video_url,
                background_image_url=item.background_image_url,
            )
            for item in result.items
        ]
        return _list_response(result, items, limit, offset)
    except (LookupError, SQLAlchemyError) as exc:
        raise _data_unavailable(db, exc) from exc
