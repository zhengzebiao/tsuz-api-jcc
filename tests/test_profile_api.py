from datetime import timedelta
import logging

import pytest

import app.deps.auth as auth_deps


def assert_request_id(response) -> None:
    assert response.headers["X-Request-ID"]


def assert_auth_log(caplog, reason: str, token: str | None = None) -> None:
    assert reason in caplog.text
    if token is not None:
        assert token not in caplog.text
        assert f"Bearer {token}" not in caplog.text


def test_profile_requires_token(client, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = client.get("/api/profile")

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"
    assert_request_id(response)
    assert_auth_log(caplog, "reason=missing_token")


def test_profile_accepts_valid_token(client, access_token_factory) -> None:
    token = access_token_factory(scope="user:read")

    response = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {"user_id": "user_123", "message": "authorized"}
    assert_request_id(response)


def test_profile_rejects_invalid_signature(client, access_token_factory, caplog) -> None:
    token = access_token_factory()
    header, payload, signature = token.split(".")
    invalid_token = f"{header}.{payload}.invalid-{signature}"

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = client.get("/api/profile", headers={"Authorization": f"Bearer {invalid_token}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"
    assert_request_id(response)
    assert_auth_log(caplog, "reason=invalid_signature", invalid_token)


def test_profile_rejects_wrong_issuer(client, access_token_factory, caplog) -> None:
    token = access_token_factory(issuer="other-issuer")

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"
    assert_request_id(response)
    assert_auth_log(caplog, "reason=invalid_issuer", token)


def test_profile_rejects_wrong_audience(client, access_token_factory, caplog) -> None:
    token = access_token_factory(audience="other-audience")

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"
    assert_request_id(response)
    assert_auth_log(caplog, "reason=invalid_audience", token)


def test_profile_rejects_expired_token(client, access_token_factory, caplog) -> None:
    token = access_token_factory(expires_delta=timedelta(minutes=-5))

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"
    assert_request_id(response)
    assert_auth_log(caplog, "reason=expired_token", token)


@pytest.mark.parametrize("claim", ["sub", "sid", "jti"])
def test_profile_rejects_missing_required_claims(client, access_token_factory, caplog, claim: str) -> None:
    token = access_token_factory(payload_overrides={claim: None})

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"
    assert_request_id(response)
    assert_auth_log(caplog, "reason=invalid_claims", token)


def test_profile_rejects_malformed_roles_claim(client, access_token_factory, caplog) -> None:
    token = access_token_factory(payload_overrides={"roles": "admin"})

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"
    assert_auth_log(caplog, "reason=invalid_claims", token)


def test_profile_rejects_malformed_roles_list_contents(client, access_token_factory, caplog) -> None:
    token = access_token_factory(payload_overrides={"roles": ["admin", 123]})

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"
    assert_auth_log(caplog, "reason=invalid_claims", token)


def test_profile_rejects_malformed_scope_claim(client, access_token_factory, caplog) -> None:
    token = access_token_factory(payload_overrides={"scope": ["user:read"]})

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"
    assert_auth_log(caplog, "reason=invalid_claims", token)


def test_profile_rejects_blacklisted_token(client, access_token_factory, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    class RejectingBlacklistService:
        def ensure_not_blacklisted(self, jti: str) -> None:
            raise ValueError("token is blacklisted")

    monkeypatch.setattr(auth_deps, "BlacklistService", RejectingBlacklistService)
    token = access_token_factory(jti="blacklisted")

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"
    assert_request_id(response)
    assert_auth_log(caplog, "reason=blacklisted_token", token)


def test_profile_rejects_revoked_session(client, access_token_factory, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    class RevokedSessionService:
        def ensure_session_active(self, sid: str) -> None:
            assert sid == "revoked-sid"
            raise ValueError("session is revoked")

    monkeypatch.setattr(auth_deps, "SessionService", RevokedSessionService)
    token = access_token_factory(sid="revoked-sid")

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["detail"] == "invalid token"
    assert_request_id(response)
    assert_auth_log(caplog, "reason=revoked_session", token)


def test_profile_rejects_insufficient_scope(client, access_token_factory, caplog) -> None:
    token = access_token_factory(scope="profile:read")

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        response = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    assert response.json()["detail"] == "insufficient scope"
    assert_request_id(response)
    assert_auth_log(caplog, "reason=insufficient_scope", token)


def test_openapi_documents_profile_bearer_auth(client) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    security_schemes = schema["components"]["securitySchemes"]
    assert security_schemes["HTTPBearer"]["scheme"] == "bearer"
    profile_operation = schema["paths"]["/api/profile"]["get"]
    assert {"HTTPBearer": []} in profile_operation["security"]


def test_require_any_scope_accepts_matching_scope() -> None:
    dependency = auth_deps.require_any_scope("admin:read", "user:read")
    user = auth_deps.CurrentUser(
        user_id="user_123",
        sid="sid_123",
        jti="jti_123",
        roles=[],
        scope="profile:read user:read",
    )

    assert dependency(user) is user


def test_require_any_scope_rejects_missing_scope(caplog) -> None:
    dependency = auth_deps.require_any_scope("admin:read", "user:read")
    user = auth_deps.CurrentUser(
        user_id="user_123",
        sid="sid_123",
        jti="jti_123",
        roles=[],
        scope="profile:read",
    )

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        with pytest.raises(auth_deps.HTTPException) as exc_info:
            dependency(user)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "insufficient scope"
    assert "reason=insufficient_scope" in caplog.text


def test_require_any_role_accepts_matching_role() -> None:
    dependency = auth_deps.require_any_role("admin", "operator")
    user = auth_deps.CurrentUser(
        user_id="user_123", sid="sid_123", jti="jti_123", roles=["operator"], scope=""
    )

    assert dependency(user) is user


def test_require_any_role_rejects_missing_role(caplog) -> None:
    dependency = auth_deps.require_any_role("admin", "operator")
    user = auth_deps.CurrentUser(
        user_id="user_123", sid="sid_123", jti="jti_123", roles=["member"], scope=""
    )

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        with pytest.raises(auth_deps.HTTPException) as exc_info:
            dependency(user)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "insufficient role"
    assert "reason=insufficient_role" in caplog.text


def test_require_role_accepts_matching_role() -> None:
    dependency = auth_deps.require_role("admin")
    user = auth_deps.CurrentUser(
        user_id="user_123", sid="sid_123", jti="jti_123", roles=["admin"], scope=""
    )

    assert dependency(user) is user


def test_require_role_rejects_missing_role(caplog) -> None:
    dependency = auth_deps.require_role("admin")
    user = auth_deps.CurrentUser(
        user_id="user_123", sid="sid_123", jti="jti_123", roles=["member"], scope=""
    )

    with caplog.at_level(logging.WARNING, logger="app.auth"):
        with pytest.raises(auth_deps.HTTPException) as exc_info:
            dependency(user)

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "insufficient role"
    assert "reason=insufficient_role" in caplog.text
