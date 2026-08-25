"""M1.2 密码和 JWT 安全工具测试。"""

import jwt
import pytest

from etl_agent.config import Settings
from etl_agent.infrastructure.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_is_not_reversible_and_verifies() -> None:
    """验证密码哈希不等于明文且可以正确区分密码。"""
    encoded = hash_password("correct-password")

    assert encoded != "correct-password"
    assert verify_password("correct-password", encoded)
    assert not verify_password("wrong-password", encoded)


def test_password_hash_rejects_short_password() -> None:
    """验证过短密码在生成阶段被拒绝。"""
    with pytest.raises(ValueError, match="至少为 8"):
        hash_password("short")


def test_access_token_round_trip_and_type_check() -> None:
    """验证访问令牌可以往返编解码且拒绝错误令牌类型。"""
    settings = Settings(_env_file=None, jwt_secret_key="test-secret-key-with-at-least-32-bytes")
    token, expires_in = create_access_token("user-1", settings)

    assert expires_in == 1800
    assert decode_access_token(token, settings)["sub"] == "user-1"

    invalid = jwt.encode(
        {"sub": "user-1", "iat": 1, "exp": 9999999999, "type": "refresh"},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(invalid, settings)
