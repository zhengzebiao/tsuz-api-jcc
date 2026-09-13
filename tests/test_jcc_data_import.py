import copy
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.jcc_data.adapters import StructuredSnapshot, build_structured_snapshot
from app.jcc_data.canonical_hash import snapshot_content_hash
from app.jcc_data.models import (
    JccAdventure,
    JccAugment,
    JccEquipment,
    JccEquipmentRecipe,
    JccGalaxy,
    JccHero,
    JccHeroTrait,
    JccSnapshot,
    JccTrait,
    JccTraitTier,
)
from app.jcc_data.repository import get_current_snapshot, import_snapshot, switch_current_snapshot
from app.jcc_data.snapshot_reader import FIELDS, REQUIRED_NAMES, read_snapshot
from app.jcc_data.validation import DataValidationError

VERSION = "18.18.test"
SEASON = "S19"
MODE = "18"


def _config() -> list[dict]:
    record = {
        "version_start_time": "2026-09-03",
        "version": VERSION,
        "season": SEASON,
        "is_newest_version": 1,
        "mode": MODE,
        "name": "自然之力",
    }
    for name, field in FIELDS.items():
        record[field] = f"/{MODE}/{VERSION}-{SEASON}/{name}.js"
    return [record]


def _wrapped(data: dict) -> dict:
    return {
        "version": VERSION,
        "season": SEASON,
        "setId": MODE,
        "time": "2026-09-02 19:19:44",
        "data": data,
    }


def _payloads() -> dict[str, object]:
    return {
        "versiondataconfig": _config(),
        "chess": _wrapped(
            {
                "hero-1": {
                    "id": "hero-1",
                    "name": "测试英雄",
                    "class": "job-1",
                    "species": "race-1",
                    "heroType": "0",
                    "mapID": "1",
                    "price": "1",
                    "initHP": "700",
                    "initAttackDamage": "40",
                    "armor": "35",
                    "magicResist": "35",
                    "attackSpeed": "0.65",
                    "attackRange": "1",
                    "initMP": "40",
                    "maxMP": "100",
                    "skillName": "测试技能",
                    "skillDesc": "技能描述",
                    "skillValueDesc": "伤害: 100/200/300",
                    "picture": "https://example.test/hero.png",
                    "skillIcon": "https://example.test/skill.png",
                },
                "pet-1": {
                    "id": "pet-1",
                    "name": "无羁绊召唤物",
                    "class": "-1",
                    "species": "-1",
                    "heroType": "1",
                    "mapID": "2",
                    "price": "0",
                    "attackSpeed": "0.5",
                },
            }
        ),
        "race": _wrapped(
            {
                "race-1": {
                    "id": "race-1",
                    "name": "测试特质",
                    "prefix": "特质前缀",
                    "maxLevel": "1",
                    "numList": "2",
                    "picture": "https://example.test/race.png",
                    "mapID": "1",
                    "setid": MODE,
                }
            }
        ),
        "job": _wrapped(
            {
                "job-1": {
                    "id": "job-1",
                    "name": "测试职业",
                    "prefix": "职业前缀",
                    "maxLevel": "1",
                    "numList": "2",
                    "picture": "https://example.test/job.png",
                    "mapID": "1",
                    "setID": MODE,
                }
            }
        ),
        "trait": _wrapped(
            {
                "trait-race-1": {
                    "id": "trait-race-1",
                    "checkId": "race-1",
                    "name": "测试特质",
                    "type": 0,
                    "level": 1,
                    "num": "2",
                    "desc": "特质描述 {0}",
                    "realDesc": "(2) 特质效果",
                },
                "trait-job-1": {
                    "id": "trait-job-1",
                    "checkId": "job-1",
                    "name": "测试职业",
                    "type": 1,
                    "level": 1,
                    "num": "2",
                    "desc": "职业描述 {0}",
                    "realDesc": "(2) 职业效果",
                },
            }
        ),
        "equip": _wrapped(
            {
                "component-1": {
                    "id": "component-1",
                    "name": "材料一",
                    "type": "基础装备",
                    "basicDesc": "+10",
                    "desc": "",
                    "picture": "https://example.test/c1.png",
                    "synthesis1": "0",
                    "synthesis2": "0",
                },
                "component-2": {
                    "id": "component-2",
                    "name": "材料二",
                    "type": "基础装备",
                    "basicDesc": "+10",
                    "desc": "",
                    "picture": "https://example.test/c2.png",
                    "synthesis1": "0",
                    "synthesis2": "0",
                },
                "equipment-1": {
                    "id": "equipment-1",
                    "name": "成装",
                    "type": "成型装备",
                    "basicDesc": "+20",
                    "desc": "装备效果",
                    "picture": "https://example.test/e1.png",
                    "synthesis1": "component-1",
                    "synthesis2": "component-2",
                },
            }
        ),
        "hex": _wrapped(
            {
                "augment-1": {
                    "id": "augment-1",
                    "name": "测试强化",
                    "level": "2",
                    "desc": "强化效果",
                    "icon": "https://example.test/a.png",
                }
            }
        ),
        "adventure": _wrapped(
            {
                "source-key-does-not-define-id": {
                    "adventureId": "adventure-1",
                    "title": "测试机制",
                    "desc": "机制效果",
                    "price": "2",
                    "category": "",
                    "logo": "https://example.test/adventure.png",
                    "video": "",
                    "bgImage": "",
                }
            }
        ),
        "galaxy": _wrapped(
            {
                "galaxy-1": {
                    "id": "galaxy-1",
                    "name": "测试奇遇",
                    "desc": "奇遇效果",
                    "logo": "https://example.test/g.png",
                    "video": "",
                    "bgImage": "",
                }
            }
        ),
        "lineup_detail_total": {"lineup_list": []},
    }


def _source_urls(config: list[dict]) -> dict[str, str]:
    current = config[0]
    urls = {"versiondataconfig": "https://game.gtimg.cn/images/lol/act/jkzlk/js/config/versiondataconfig.js"}
    for name, field in FIELDS.items():
        urls[name] = "https://game.gtimg.cn/images/lol/act/jkzlk/js" + current[field]
    urls["lineup_detail_total"] = (
        "https://game.gtimg.cn/images/lol/act/jkzlkauto/json/lineupJson/m19/11/18/lineup_detail_total.json"
    )
    return urls


def _write_snapshot(directory: Path, payloads: dict[str, object], *, revision: int = 1) -> None:
    directory.mkdir(parents=True)
    for name in REQUIRED_NAMES:
        (directory / f"{name}.json").write_text(json.dumps(payloads[name], ensure_ascii=False), encoding="utf-8")
    urls = _source_urls(payloads["versiondataconfig"])
    content_hash = snapshot_content_hash({name: payloads[name] for name in ("versiondataconfig", *FIELDS)})
    manifest = {
        "mode": MODE,
        "mode_name": "自然之力",
        "season": SEASON,
        "version": VERSION,
        "base_version": f"{VERSION}-{SEASON}",
        "revision": revision,
        "raw_directory_name": directory.name,
        "content_hash": content_hash,
        "set_id": MODE,
        "source_updated_at": "2026-09-02 19:19:44",
        "sources": {
            name: {
                "url": urls[name],
                "file": f"{name}.json",
                "record_count": (
                    len(payloads[name])
                    if name == "versiondataconfig"
                    else len(payloads[name]["lineup_list"])
                    if name == "lineup_detail_total"
                    else len(payloads[name]["data"])
                ),
            }
            for name in REQUIRED_NAMES
        },
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _structured(tmp_path: Path, payloads: dict[str, object] | None = None, *, revision: int = 1) -> StructuredSnapshot:
    payloads = copy.deepcopy(payloads or _payloads())
    name = f"{VERSION}-{SEASON}" if revision == 1 else f"{VERSION}-{SEASON}-r{revision}"
    directory = tmp_path / name
    _write_snapshot(directory, payloads, revision=revision)
    return build_structured_snapshot(read_snapshot(directory))


def test_adapter_builds_complete_structured_snapshot(tmp_path: Path) -> None:
    structured = _structured(tmp_path)

    assert len(structured.heroes) == 2
    hero = next(item for item in structured.heroes if item.external_id == "hero-1")
    assert str(hero.attack_speed) == "0.65"
    assert hero.skill_values == {"skillValueDesc": "伤害: 100/200/300"}
    assert [(item.relation_kind, item.trait_external_id) for item in structured.hero_traits] == [
        ("job", "job-1"),
        ("race", "race-1"),
    ]
    assert [(item.kind, item.external_id, len(item.tiers)) for item in structured.traits] == [
        ("race", "race-1", 1),
        ("job", "job-1", 1),
    ]
    assert structured.equipment_recipes[0].first_component_external_id == "component-1"
    assert structured.adventures[0].external_id == "adventure-1"


def test_adapter_rejects_unknown_non_sentinel_relationship(tmp_path: Path) -> None:
    payloads = _payloads()
    payloads["chess"]["data"]["hero-1"]["class"] = "unknown-job"
    directory = tmp_path / f"{VERSION}-{SEASON}"
    _write_snapshot(directory, payloads)

    with pytest.raises(DataValidationError, match="unknown job"):
        build_structured_snapshot(read_snapshot(directory))


def test_reader_rejects_snapshot_for_other_configured_mode(tmp_path: Path) -> None:
    _structured(tmp_path)
    directory = tmp_path / f"{VERSION}-{SEASON}"

    with pytest.raises(ValueError, match="expected exactly one current mode=other"):
        read_snapshot(directory, expected_mode="other")


def test_import_is_idempotent_and_publishes_current(db: Session, tmp_path: Path) -> None:
    structured = _structured(tmp_path)

    first = import_snapshot(db, structured)
    second = import_snapshot(db, structured)

    assert first.status == "updated"
    assert second.status == "skipped"
    assert first.snapshot_id == second.snapshot_id
    assert get_current_snapshot(db, MODE).id == first.snapshot_id
    assert db.scalar(select(func.count()).select_from(JccSnapshot)) == 1
    assert db.scalar(select(func.count()).select_from(JccHero)) == 2
    assert db.scalar(select(func.count()).select_from(JccTrait)) == 2
    assert db.scalar(select(func.count()).select_from(JccTraitTier)) == 2
    assert db.scalars(select(JccTraitTier).order_by(JccTraitTier.id)).all()[0].tier_order == 1
    assert db.scalar(select(func.count()).select_from(JccHeroTrait)) == 2
    assert db.scalar(select(func.count()).select_from(JccEquipment)) == 3
    assert db.scalar(select(func.count()).select_from(JccEquipmentRecipe)) == 1
    assert db.scalar(select(func.count()).select_from(JccAugment)) == 1
    assert db.scalar(select(func.count()).select_from(JccAdventure)) == 1
    assert db.scalar(select(func.count()).select_from(JccGalaxy)) == 1


def test_existing_snapshot_must_be_complete_before_reuse(db: Session, tmp_path: Path) -> None:
    structured = _structured(tmp_path)
    first = import_snapshot(db, structured)
    db.execute(
        JccHero.__table__.delete().where(
            JccHero.snapshot_id == first.snapshot_id,
            JccHero.external_id == "pet-1",
        )
    )
    db.commit()

    with pytest.raises(RuntimeError, match="incomplete"):
        import_snapshot(db, structured)


def test_new_revision_switches_current_and_old_snapshot_remains(db: Session, tmp_path: Path) -> None:
    first = _structured(tmp_path / "first")
    first_result = import_snapshot(db, first)
    payloads = _payloads()
    payloads["hex"]["data"]["augment-1"]["desc"] = "修订后的强化效果"
    second = _structured(tmp_path / "second", payloads, revision=2)

    second_result = import_snapshot(db, second)

    assert second_result.status == "updated"
    assert second_result.snapshot_id != first_result.snapshot_id
    assert get_current_snapshot(db, MODE).id == second_result.snapshot_id
    assert db.scalar(select(func.count()).select_from(JccSnapshot)) == 2
    db.commit()
    switch_current_snapshot(db, MODE, first_result.snapshot_id)
    assert get_current_snapshot(db, MODE).id == first_result.snapshot_id


def test_failed_import_rolls_back_and_keeps_current(db: Session, tmp_path: Path) -> None:
    first = _structured(tmp_path / "first")
    first_result = import_snapshot(db, first)
    payloads = _payloads()
    payloads["hex"]["data"]["augment-1"]["desc"] = "新版本"
    second = _structured(tmp_path / "second", payloads, revision=2)
    bad = StructuredSnapshot(
        metadata=second.metadata,
        heroes=second.heroes,
        traits=second.traits,
        hero_traits=second.hero_traits,
        equipment=second.equipment,
        equipment_recipes=[
            copy.copy(second.equipment_recipes[0]).__class__(
                "missing-equipment",
                second.equipment_recipes[0].first_component_external_id,
                second.equipment_recipes[0].second_component_external_id,
            )
        ],
        augments=second.augments,
        adventures=second.adventures,
        galaxies=second.galaxies,
    )

    with pytest.raises(KeyError):
        import_snapshot(db, bad)

    assert get_current_snapshot(db, MODE).id == first_result.snapshot_id
    assert db.scalar(select(func.count()).select_from(JccSnapshot)) == 1
