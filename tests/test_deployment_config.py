from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT_DIR / "docker-compose.deploy.yml"
DEPLOY_WORKFLOW = ROOT_DIR / ".github/workflows/deploy.yml"
SYNC_WORKFLOW = ROOT_DIR / ".github/workflows/sync-jcc-data.yml"
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


def test_deploy_compose_persists_raw_snapshots() -> None:
    compose = _read(COMPOSE_FILE)
    deploy_workflow = _read(DEPLOY_WORKFLOW)

    assert "- raw_data:/app/raw" in compose
    assert "volumes:\n  raw_data:\n    name: ${JCC_DATA_VOLUME_NAME:-jcc_raw_data}\n" in compose
    assert "JCC_DATA_VOLUME_NAME: ${{ vars.JCC_DATA_VOLUME_NAME }}" in deploy_workflow
    assert "tsuz-api-jcc-{os.environ['DEPLOY_ENV']}-raw" in deploy_workflow


def test_sync_workflow_scans_both_environments_without_lifecycle_changes() -> None:
    workflow = _read(SYNC_WORKFLOW)

    assert 'cron: "17 */6 * * *"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "environment:" in workflow
    assert 'options:\n          - test\n          - product' in workflow
    assert "'[\"test\",\"product\"]'" in workflow
    assert "environment: ${{ matrix.environment }}" in workflow
    assert "group: jcc-runtime-${{ github.repository }}-${{ matrix.environment }}" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "fromJSON(github.event_name == 'workflow_dispatch'" in workflow
    assert 'health" != "healthy"' in workflow
    assert "exec -T api pdm run sync-jcc-data --force-refresh" in workflow

    for command in ("build", "push", "pull", "up", "restart", "run"):
        assert f"docker compose {command}" not in workflow
        assert f'"${{compose[@]}}" {command}' not in workflow


def test_deploy_scans_only_after_normal_release() -> None:
    workflow = _read(DEPLOY_WORKFLOW)
    start = workflow.index("- name: Scan JCC data after normal release")
    end = workflow.index("- name: Run deployment smoke test", start)
    scan_step = workflow[start:end]

    assert "if: ${{ env.SHOULD_BUILD == 'true' }}" in scan_step
    assert "exec -T api pdm run sync-jcc-data --force-refresh" in scan_step
    assert "group: jcc-runtime-${{ github.repository }}-" in workflow
    assert "docker compose run" not in scan_step
    assert "docker compose up" not in scan_step
    assert workflow.count("pdm run sync-jcc-data --force-refresh") == 1
