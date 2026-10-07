from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_service_status() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["X-Request-ID"]


def test_api_documentation_uses_jcc_prefix() -> None:
    assert app.docs_url == "/jcc/docs"
    assert app.redoc_url == "/jcc/redoc"
    assert app.openapi_url == "/jcc/openapi.json"
