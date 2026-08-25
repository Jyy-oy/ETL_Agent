"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "etl-agent"
    app_env: str = "development"
    log_level: str = "INFO"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    database_url: str = "postgresql+asyncpg://etl_agent:etl_agent_dev@localhost:5432/etl_agent"
    database_sync_url: str = "postgresql+psycopg://etl_agent:etl_agent_dev@localhost:5432/etl_agent"
    langgraph_checkpoint_database_url: str = (
        "postgresql://etl_agent:etl_agent_dev@localhost:5432/etl_agent"
    )

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    replay_guard_redis_url: str = "redis://localhost:6379/3"

    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_region: str = "us-east-1"
    minio_secure: bool = False
    minio_bucket: str = "etl-agent"
    local_file_storage_path: str = "./var/files"
    max_upload_size_bytes: int = 1_073_741_824

    vault_addr: str = "http://localhost:8200"
    vault_token: str = "dev-only-token"
    vault_namespace: str = ""
    vault_kv_mount: str = "secret"
    vault_kv_prefix: str = "etl-agent"
    secret_provider: str = "vault"

    llm_provider: str = "openai_compatible"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_request_timeout_seconds: int = 60
    llm_max_retries: int = 2

    seatunnel_zeta_endpoint: str = "http://localhost:5801"
    health_check_timeout_seconds: float = 5.0

    jwt_secret_key: str = "replace-with-a-random-32-byte-secret"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    @property
    def cors_origins_list(self) -> list[str]:
        """将逗号分隔的跨域来源配置清洗为字符串列表。"""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def asyncpg_database_url(self) -> str:
        """将 SQLAlchemy 异步驱动前缀转换为 asyncpg 可识别的连接串。"""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """读取环境变量并缓存全局应用配置，避免重复解析配置文件。"""
    return Settings()
