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


def test_deploy_propagates_agent_llm_and_rag_configuration() -> None:
    workflow = _read(DEPLOY_WORKFLOW)
    expected_variables = (
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "LLM_TIMEOUT_SECONDS",
        "LLM_MAX_TOKENS",
        "AGENT_QUEUE_MAXSIZE",
        "AGENT_EXECUTION_TIMEOUT_SECONDS",
        "AGENT_SHUTDOWN_TIMEOUT_SECONDS",
        "AGENT_SSE_HEARTBEAT_SECONDS",
        "AGENT_CONTEXT_MESSAGES",
        "AGENT_MAX_TOOL_ITERATIONS",
        "AGENT_TOOL_TIMEOUT_SECONDS",
        "AGENT_TOOL_INPUT_MAX_BYTES",
        "AGENT_TOOL_OUTPUT_MAX_BYTES",
        "AGENT_SOURCE_EXCERPT_MAX_CHARS",
        "AGENT_RATE_LIMIT_ENABLED",
        "AGENT_RATE_LIMIT_WINDOW_SECONDS",
        "AGENT_RATE_LIMIT_MESSAGES",
        "AGENT_SSE_REPLAY_MAX_EVENTS",
        "AGENT_SSE_MAX_CONNECTIONS_PER_USER",
        "AGENT_SUMMARY_ENABLED",
        "AGENT_SUMMARY_TRIGGER_CHARS",
        "RAG_ENABLED",
        "RAG_EMBEDDING_PROVIDER",
        "RAG_EMBEDDING_BASE_URL",
        "RAG_EMBEDDING_TIMEOUT_SECONDS",
        "RAG_EMBEDDING_MODEL",
        "RAG_EMBEDDING_DIMENSION",
        "RAG_EMBEDDING_BATCH_SIZE",
        "RAG_RETRIEVAL_DEFAULT_LIMIT",
        "RAG_RETRIEVAL_MAX_LIMIT",
        "LLM_INPUT_PRICE_PER_MILLION",
        "LLM_OUTPUT_PRICE_PER_MILLION",
        "LLM_PRICING_KEY",
    )
    for variable in expected_variables:
        assert f'"{variable}": os.environ.get("{variable}")' in workflow
    for secret in ("LLM_API_KEY", "RAG_EMBEDDING_API_KEY"):
        assert f"{secret}: ${{{{ secrets.{secret} }}}}" in workflow
        assert f'"{secret}": os.environ.get("{secret}")' in workflow
    assert 'LLM_BASE_URL: ${{ vars.LLM_BASE_URL }}' in workflow
    assert 'RAG_EMBEDDING_BASE_URL: ${{ vars.RAG_EMBEDDING_BASE_URL }}' in workflow


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
