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
