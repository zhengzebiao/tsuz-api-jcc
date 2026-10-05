from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "test"
    debug: bool = True
    log_level: str = "debug"
    log_format: str = "json"
    request_id_header: str = "X-Request-ID"
    service_name: str = "backend-api"
    api_prefix: str = "/api"

    database_url: str = "postgresql+psycopg://test_user:test_password@localhost:5432/test_app"
    db_sslmode: str = "disable"
    redis_url: str = "redis://localhost:6379/0"
    main_redis_url: str | None = None
    redis_key_prefix: str = "auth:test:"

    jwt_algorithm: str = "RS256"
    jwt_issuer: str = "auth-service-test"
    jwt_audience: str = "backend-api-test"
    jwt_public_key: str = ""

    service_token_issuer: str = "tsuz-api-main"
    service_token_audience: str = ""
    service_token_public_key: str = ""
    service_token_clock_skew_seconds: int = 5
    jcc_app_id: str = ""
    jcc_app_secret: str = ""
    main_app_id: str = ""
    main_token_url: str = "http://127.0.0.1:8000/internal/oauth/token"
    main_api_base_url: str = "http://127.0.0.1:8000"
    internal_http_timeout_seconds: float = 10.0

    jcc_data_raw_dir: str = "raw"
    jcc_data_mode: str = "18"
    jcc_data_mode_name: str = "自然之力"
    jcc_data_sync_timeout_seconds: float = 30.0
    jcc_data_sync_retries: int = 2

    llm_provider: str = "openai_compatible"
    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_timeout_seconds: float = 120.0
    llm_max_tokens: int = 2048
    agent_queue_maxsize: int = 1
    agent_execution_timeout_seconds: float = 180.0
    agent_shutdown_timeout_seconds: float = 10.0
    agent_sse_heartbeat_seconds: float = 15.0
    agent_context_messages: int = 10
    agent_max_tool_iterations: int = 8
    agent_tool_timeout_seconds: float = 15.0
    agent_tool_input_max_bytes: int = 16384
    agent_tool_output_max_bytes: int = 64000
    agent_source_excerpt_max_chars: int = 1000
    agent_rate_limit_enabled: bool = False
    agent_rate_limit_window_seconds: float = 60.0
    agent_rate_limit_messages: int = 30
    agent_sse_replay_max_events: int = 100
    agent_summary_enabled: bool = False
    agent_summary_trigger_chars: int = 24000
    llm_input_price_per_million: float | None = None
    llm_output_price_per_million: float | None = None
    llm_pricing_key: str = ""
    rag_enabled: bool = True
    rag_embedding_provider: str = "fake"
    rag_embedding_model: str = "fake-test-1024"
    rag_embedding_dimension: int = 1024
    rag_embedding_base_url: str = ""
    rag_embedding_api_key: str = ""
    rag_embedding_timeout_seconds: float = 120.0
    rag_embedding_batch_size: int = 32
    rag_retrieval_default_limit: int = 5
    rag_retrieval_max_limit: int = 20

    token_blacklist_prefix: str = "auth:test:blacklist:jti:"
    session_prefix: str = "auth:test:session:"

    cors_allow_origins: str = "http://localhost:5173"
    cors_allow_credentials: bool = True

    openapi_enabled: bool = True
    docs_enabled: bool = True
    redoc_enabled: bool = True

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]


settings = Settings()
