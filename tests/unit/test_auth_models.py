from etl_agent.api.auth_models import RegisterRequest
from etl_agent.infrastructure.models import ProjectRole


def test_register_request_supports_checker_assignment() -> None:
    """验证注册请求可以携带项目编码和 Checker 职责槽。"""
    payload = RegisterRequest(
        username="checker_demo",
        display_name="开发 Checker",
        password="Test1234!",
        project_code="etl_learning",
        project_role=ProjectRole.CHECKER_1,
    )

    assert payload.project_code == "etl_learning"
    assert payload.project_role is ProjectRole.CHECKER_1


def test_register_request_keeps_plain_account_compatibility() -> None:
    """验证不填写项目职责时仍可注册普通开发账号。"""
    payload = RegisterRequest(
        username="maker_demo",
        display_name="开发用户",
        password="Test1234!",
    )

    assert payload.project_code is None
    assert payload.project_role is None
