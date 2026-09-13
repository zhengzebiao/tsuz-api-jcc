import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import jcc as jcc_api
from app.api.jcc import router as jcc_router
from app.core.database import Base, get_db
from app.core.logging import RequestIdMiddleware
from app.jcc_data import repository
from app.jcc_data.adapters import (
    AdventureData,
    AugmentData,
    EquipmentData,
    EquipmentRecipeData,
    GalaxyData,
    HeroData,
    HeroTraitData,
    SnapshotData,
    StructuredSnapshot,
    TraitData,
    TraitTierData,
)
from app.jcc_data.models import JccSnapshot
from app.jcc_data.repository import import_snapshot, switch_current_snapshot
from tests.conftest import TEST_PRIVATE_KEY

MODE = "18"


def _source() -> dict:
    return {}


def _snapshot(*, revision: int = 1, suffix: str = "") -> StructuredSnapshot:
    metadata = SnapshotData(
        mode=MODE,
        mode_name="自然之力",
        season="S19",
        version=f"18.18.{revision}",
        revision=revision,
        set_id=MODE,
        raw_directory_name=f"18.18.{revision}-S19",
        content_hash=str(revision) * 64,
        source_updated_at=f"2026-09-0{revision} 12:00:00",
        version_start_time=f"2026-09-0{revision}",
        source_manifest={"sources": {"chess": {}, "race": {}, "job": {}}},
    )
    traits = [
        TraitData(
            kind="race",
            external_id="race-1",
            name=f"森林{suffix}",
            prefix="森林前缀",
            max_level=2,
            activation_list=[2, 4],
            image_url="https://example.test/race.png",
            map_id=1,
            source_attributes=_source(),
            tiers=[
                TraitTierData("tier-race-1", 1, 2, 1, "两人效果", "(2) 两人效果", _source()),
                TraitTierData("tier-race-2", 2, 4, 2, "四人效果", "(4) 四人效果", _source()),
            ],
        ),
        TraitData(
            kind="job",
            external_id="job-1",
            name=f"护卫{suffix}",
            prefix="护卫前缀",
            max_level=1,
            activation_list=[2],
            image_url="https://example.test/job.png",
            map_id=1,
            source_attributes=_source(),
            tiers=[TraitTierData("tier-job-1", 1, 2, 1, "护卫效果", "(2) 护卫效果", _source())],
        ),
        TraitData(
            kind="race",
            external_id="race-2",
            name="星界",
            prefix=None,
            max_level=1,
            activation_list=[1],
            image_url=None,
            map_id=1,
            source_attributes=_source(),
            tiers=[TraitTierData("tier-race-3", 1, 1, 1, None, "(1) 星界效果", _source())],
        ),
    ]
    heroes = [
        HeroData(
            external_id="hero-2",
            name=f"贝塔{suffix}",
            price=2,
            hero_type="0",
            map_id=1,
            health=800,
            attack_damage=50,
            armor=40,
            magic_resist=40,
            attack_speed=Decimal("0.70"),
            attack_range=1,
            initial_mana=20,
            max_mana=80,
            skill_name="护盾",
            skill_description="获得护盾",
            skill_values={"skillValueDesc": "100/200/300"},
            image_url="https://example.test/beta.png",
            skill_icon_url="https://example.test/beta-skill.png",
            source_attributes=_source(),
        ),
        HeroData(
            external_id="hero-1",
            name=f"阿尔法{suffix}",
            price=1,
            hero_type="0",
            map_id=1,
            health=700,
            attack_damage=40,
            armor=30,
            magic_resist=30,
            attack_speed=Decimal("0.65"),
            attack_range=4,
            initial_mana=40,
            max_mana=100,
            skill_name="星火",
            skill_description="造成伤害",
            skill_values={"skillBriefValue": "100/200/300"},
            image_url="https://example.test/alpha.png",
            skill_icon_url="https://example.test/alpha-skill.png",
            source_attributes=_source(),
        ),
    ]
    relations = [
        HeroTraitData("hero-1", "job-1", "job", 0),
        HeroTraitData("hero-1", "race-1", "race", 0),
        HeroTraitData("hero-1", "race-2", "race", 1),
        HeroTraitData("hero-2", "job-1", "job", 0),
        HeroTraitData("hero-2", "race-1", "race", 0),
    ]
    equipment = [
        EquipmentData("component-2", "拳套", "基础装备", "+10%", None, None, _source()),
        EquipmentData("equipment-1", f"无尽之刃{suffix}", "成型装备", "+20", "暴击效果", None, _source()),
        EquipmentData("component-1", "暴风之剑", "基础装备", "+10", None, None, _source()),
        EquipmentData("equipment-2", "双剑", "成型装备", None, "重复材料", None, _source()),
    ]
    return StructuredSnapshot(
        metadata=metadata,
        heroes=heroes,
        traits=traits,
        hero_traits=relations,
        equipment=equipment,
        equipment_recipes=[
            EquipmentRecipeData("equipment-1", "component-1", "component-2"),
            EquipmentRecipeData("equipment-2", "component-1", "component-1"),
        ],
        augments=[AugmentData("augment-1", f"存心失利{suffix}", 2, "失败后获得金币", None, _source())],
        adventures=[
            AdventureData(
                "adventure-1",
                f"余震{suffix}",
                "晕眩敌人",
                2,
                None,
                "https://example.test/adventure.png",
                None,
                None,
                _source(),
            )
        ],
        galaxies=[
            GalaxyData(
                "galaxy-1",
                f"金金金之旅{suffix}",
                "强化符文变为黄金阶",
                "https://example.test/galaxy.png",
                None,
                None,
                _source(),
            )
        ],
    )


@pytest.fixture
def jcc_context() -> Iterator[tuple[TestClient, DbSession]]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = session_factory()
    import_snapshot(db, _snapshot())

    def override_db() -> Iterator[DbSession]:
        yield db

    application = FastAPI()
    application.add_middleware(RequestIdMiddleware)
    application.include_router(jcc_router)
    application.dependency_overrides[get_db] = override_db
    try:
        with TestClient(application) as client:
            yield client, db
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def auth_headers(access_token_factory) -> dict[str, str]:
    token = access_token_factory(scope="user:read jcc:data:read")
    return {"Authorization": f"Bearer {token}"}


def test_snapshot_returns_public_metadata_and_source_count(jcc_context, auth_headers) -> None:
    client, _ = jcc_context

    response = client.get("/jcc/snapshot", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "snapshot": {
            "mode": MODE,
            "mode_name": "自然之力",
            "season": "S19",
            "version": "18.18.1",
            "revision": 1,
            "content_hash": "1" * 64,
            "source_updated_at": "2026-09-01 12:00:00",
        },
        "data": {"source_count": 3},
    }
    assert "raw_directory_name" not in response.text
    assert "source_manifest" not in response.text
    assert "snapshot_id" not in response.text


def test_heroes_are_stably_paginated_with_full_relationships(jcc_context, auth_headers) -> None:
    client, _ = jcc_context

    response = client.get("/jcc/heroes?limit=1&offset=0", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["limit"] == 1
    assert payload["offset"] == 0
    assert [item["id"] for item in payload["items"]] == ["hero-1"]
    hero = payload["items"][0]
    assert hero["attack_speed"] == 0.65
    assert hero["traits"] == [
        {"id": "race-1", "name": "森林", "kind": "race"},
        {"id": "race-2", "name": "星界", "kind": "race"},
    ]
    assert hero["classes"] == [{"id": "job-1", "name": "护卫", "kind": "job"}]
    assert hero["skill_values"] == {"skillBriefValue": "100/200/300"}


def test_hero_filters_combine_without_duplicate_totals(jcc_context, auth_headers) -> None:
    client, _ = jcc_context

    response = client.get(
        "/jcc/heroes",
        params={"name": "阿尔", "trait_id": "race-2", "class_id": "job-1", "price": 1},
        headers=auth_headers,
    )
    wrong_kind = client.get("/jcc/heroes?trait_id=job-1", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["id"] for item in response.json()["items"]] == ["hero-1"]
    assert wrong_kind.status_code == 200
    assert wrong_kind.json()["total"] == 0


def test_hero_detail_is_complete_and_unknown_is_fixed_404(jcc_context, auth_headers) -> None:
    client, _ = jcc_context

    response = client.get("/jcc/heroes/hero-2", headers=auth_headers)
    missing = client.get("/jcc/heroes/unknown", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["data"]["name"] == "贝塔"
    assert response.json()["data"]["health"] == 800
    assert missing.status_code == 404
    assert missing.json() == {"detail": "JCC_HERO_NOT_FOUND"}


def test_traits_return_complete_ordered_tiers_and_filters(jcc_context, auth_headers) -> None:
    client, _ = jcc_context

    response = client.get("/jcc/traits?kind=race&name=森林", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["total"] == 1
    trait = response.json()["items"][0]
    assert trait["id"] == "race-1"
    assert trait["activation_list"] == [2, 4]
    assert [tier["tier_order"] for tier in trait["tiers"]] == [1, 2]
    assert trait["tiers"][1]["real_description"] == "(4) 四人效果"


def test_equipment_returns_ordered_components_and_empty_components(jcc_context, auth_headers) -> None:
    client, _ = jcc_context

    response = client.get("/jcc/equipment?type=成型装备", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["total"] == 2
    items = {item["id"]: item for item in response.json()["items"]}
    assert items["equipment-1"]["components"] == [
        {"id": "component-1", "name": "暴风之剑"},
        {"id": "component-2", "name": "拳套"},
    ]
    assert items["equipment-2"]["components"] == [
        {"id": "component-1", "name": "暴风之剑"},
        {"id": "component-1", "name": "暴风之剑"},
    ]
    basic = client.get("/jcc/equipment?name=拳套", headers=auth_headers)
    assert basic.json()["items"][0]["components"] == []


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/jcc/augments?name=失利&level=2", {"id": "augment-1", "name": "存心失利", "level": 2}),
        ("/jcc/adventures?title=余震&price=2", {"id": "adventure-1", "title": "余震", "price": 2}),
        ("/jcc/galaxies?name=金金", {"id": "galaxy-1", "name": "金金金之旅"}),
    ],
)
def test_simple_resource_lists_return_full_items_and_filters(jcc_context, auth_headers, path, expected) -> None:
    client, _ = jcc_context

    response = client.get(path, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item | expected == item
    assert "description" in item


def test_current_switch_changes_metadata_and_data_together(jcc_context, auth_headers) -> None:
    client, db = jcc_context
    first = client.get("/jcc/heroes/hero-1", headers=auth_headers).json()
    first_snapshot_id = db.scalar(select(JccSnapshot.id).where(JccSnapshot.revision == 1))
    db.commit()
    second_result = import_snapshot(db, _snapshot(revision=2, suffix="新版"))

    second = client.get("/jcc/heroes/hero-1", headers=auth_headers).json()

    assert first["snapshot"]["version"] == "18.18.1"
    assert first["data"]["name"] == "阿尔法"
    assert second_result.snapshot_id != first_snapshot_id
    assert second["snapshot"]["version"] == "18.18.2"
    assert second["snapshot"]["revision"] == 2
    assert second["data"]["name"] == "阿尔法新版"


def test_query_after_current_lookup_stays_on_fixed_snapshot(
    jcc_context,
    auth_headers,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = jcc_context
    first_snapshot_id = db.scalar(select(JccSnapshot.id).where(JccSnapshot.revision == 1))
    db.commit()
    second_result = import_snapshot(db, _snapshot(revision=2, suffix="新版"))
    db.commit()
    switch_current_snapshot(db, MODE, first_snapshot_id)

    original_page = repository._page

    def switch_during_request(session, statement, *, limit, offset):
        result = original_page(session, statement, limit=limit, offset=offset)
        session.commit()
        switch_current_snapshot(session, MODE, second_result.snapshot_id)
        return result

    monkeypatch.setattr(repository, "_page", switch_during_request)

    response = client.get("/jcc/heroes?limit=1", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["snapshot"]["revision"] == 1
    assert response.json()["items"][0]["name"] == "阿尔法"
    assert response.json()["items"][0]["traits"][0]["name"] == "森林"


def test_database_error_returns_fixed_503_without_sql_details(
    jcc_context,
    auth_headers,
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    client, _ = jcc_context

    def fail_query(*_args, **_kwargs):
        raise OperationalError("SELECT secret", {"password": "database-secret"}, RuntimeError("offline"))

    monkeypatch.setattr(jcc_api, "list_current_galaxies", fail_query)

    with caplog.at_level(logging.ERROR, logger="app.jcc"):
        response = client.get("/jcc/galaxies", headers=auth_headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "JCC_DATA_UNAVAILABLE"}
    assert "error_type=OperationalError" in caplog.text
    assert "SELECT secret" not in caplog.text
    assert "database-secret" not in caplog.text


def test_database_error_remains_fixed_503_when_rollback_fails(
    jcc_context,
    auth_headers,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db = jcc_context

    def fail_query(*_args, **_kwargs):
        raise OperationalError("SELECT secret", {}, RuntimeError("offline"))

    def fail_rollback():
        raise OperationalError("ROLLBACK", {}, RuntimeError("offline"))

    monkeypatch.setattr(jcc_api, "list_current_galaxies", fail_query)
    monkeypatch.setattr(db, "rollback", fail_rollback)

    response = client.get("/jcc/galaxies", headers=auth_headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "JCC_DATA_UNAVAILABLE"}


def test_no_current_returns_fixed_503(auth_headers) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    def override_db() -> Iterator[DbSession]:
        yield session

    application = FastAPI()
    application.include_router(jcc_router)
    application.dependency_overrides[get_db] = override_db
    try:
        with TestClient(application) as client:
            response = client.get("/jcc/snapshot", headers=auth_headers)
        assert response.status_code == 503
        assert response.json() == {"detail": "JCC_DATA_UNAVAILABLE"}
    finally:
        session.close()
        engine.dispose()


@pytest.mark.parametrize(
    "path",
    [
        "/jcc/heroes?limit=0",
        "/jcc/heroes?limit=101",
        "/jcc/heroes?offset=-1",
        "/jcc/heroes?name=%20%20",
        "/jcc/heroes?trait_id=bad%20id",
        "/jcc/heroes/bad%20id",
        "/jcc/traits?kind=other",
        "/jcc/augments?level=-1",
    ],
)
def test_invalid_parameters_return_422(jcc_context, auth_headers, path) -> None:
    client, _ = jcc_context
    assert client.get(path, headers=auth_headers).status_code == 422


def test_all_routes_require_user_scope(jcc_context, access_token_factory, auth_headers) -> None:
    client, _ = jcc_context

    missing = client.get("/jcc/heroes")
    insufficient = client.get(
        "/jcc/heroes",
        headers={"Authorization": f"Bearer {access_token_factory(scope='user:read')}"},
    )
    valid = client.get("/jcc/heroes", headers=auth_headers)

    assert missing.status_code == 401
    assert missing.json() == {"detail": "invalid token"}
    assert insufficient.status_code == 403
    assert insufficient.json() == {"detail": "insufficient scope"}
    assert valid.status_code == 200


def test_service_token_is_not_accepted_as_user_token(jcc_context) -> None:
    client, _ = jcc_context
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": "tsuz-api-main",
            "sub": "app-main",
            "aud": "app_jcc",
            "token_use": "service",
            "scope": "jcc:data:read",
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "jti": "service-jti",
        },
        TEST_PRIVATE_KEY,
        algorithm="RS256",
    )

    response = client.get("/jcc/snapshot", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid token"}


def test_openapi_contains_only_intended_read_routes(jcc_context) -> None:
    client, _ = jcc_context

    schema = client.get("/openapi.json").json()
    jcc_paths = {path: operations for path, operations in schema["paths"].items() if path.startswith("/jcc")}

    assert set(jcc_paths) == {
        "/jcc/snapshot",
        "/jcc/heroes",
        "/jcc/heroes/{hero_id}",
        "/jcc/traits",
        "/jcc/equipment",
        "/jcc/augments",
        "/jcc/adventures",
        "/jcc/galaxies",
    }
    assert all(set(operations) == {"get"} for operations in jcc_paths.values())
    assert all({"HTTPBearer": []} in operations["get"]["security"] for operations in jcc_paths.values())
    assert "/jcc/equipment/{equipment_id}" not in schema["paths"]
    assert all(not path.startswith("/internal/v1/jcc") for path in schema["paths"])


def test_jcc_request_log_uses_route_and_safe_snapshot_context(jcc_context, auth_headers, caplog) -> None:
    client, _ = jcc_context

    with caplog.at_level(logging.INFO, logger="app.request"):
        response = client.get(
            "/jcc/heroes/hero-1",
            headers={**auth_headers, "X-Request-ID": "req-jcc-1"},
        )

    assert response.status_code == 200
    record = next(record for record in caplog.records if record.name == "app.request")
    assert record.request_id == "req-jcc-1"
    assert "route=/jcc/heroes/{hero_id}" in record.message
    assert "snapshot_version=18.18.1" in record.message
    assert "snapshot_revision=1" in record.message
    assert auth_headers["Authorization"] not in caplog.text
    assert "阿尔法" not in caplog.text
