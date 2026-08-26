from etl_agent.infrastructure.models import Base


def test_m1_identity_tables_are_registered() -> None:
    """验证 M1 身份与项目领域表已注册到 ORM 元数据。"""
    assert {
        "users",
        "projects",
        "project_memberships",
        "project_role_grants",
        "file_assets",
        "benchmark_runs",
    } <= set(Base.metadata.tables)
