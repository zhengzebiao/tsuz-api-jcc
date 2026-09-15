from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT_DIR / "docker-compose.deploy.yml"
NGINX_CONFIG = ROOT_DIR / "nginx/default.conf"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_deploy_compose_uses_the_jcc_api_network_alias() -> None:
    compose = _read(COMPOSE_FILE)

    assert "external: true" in compose
    assert "aliases:" in compose
    assert "- jcc-api" in compose


def test_nginx_uses_the_jcc_api_network_alias() -> None:
    nginx = _read(NGINX_CONFIG)

    assert nginx.count("proxy_pass http://jcc-api:8000") == 2
    assert "proxy_pass http://api:8000" not in nginx
