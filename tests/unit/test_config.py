from etl_agent.config import Settings


def test_settings_parse_cors_origins() -> None:
    """验证跨域来源配置会拆分并去除首尾空白。"""
    settings = Settings(_env_file=None, cors_origins="http://localhost:5173, http://localhost:3000")

    assert settings.cors_origins_list == ["http://localhost:5173", "http://localhost:3000"]


def test_settings_normalize_asyncpg_scheme() -> None:
    """验证数据库连接串可以转换为 asyncpg 探针所需的格式。"""
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://user:pass@db:5432/app",
    )

    assert settings.asyncpg_database_url == "postgresql://user:pass@db:5432/app"
