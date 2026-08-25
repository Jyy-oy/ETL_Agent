"""本地开发账号的密码哈希和 JWT 令牌工具。"""

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from etl_agent.config import Settings

_PASSWORD_SCHEME = "scrypt"
_PASSWORD_N = 2**14
_PASSWORD_R = 8
_PASSWORD_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32


def _encode_password_component(value: bytes) -> str:
    """将密码哈希二进制片段编码为可安全存储的无填充 Base64。"""
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_password_component(value: str) -> bytes:
    """将密码哈希中的无填充 Base64 片段还原为二进制。"""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    """使用带随机盐的 scrypt 生成可存储的密码哈希字符串。"""
    if len(password) < 8:
        raise ValueError("密码长度至少为 8 个字符")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_PASSWORD_N,
        r=_PASSWORD_R,
        p=_PASSWORD_P,
        dklen=_KEY_BYTES,
    )
    return (
        f"{_PASSWORD_SCHEME}${_PASSWORD_N}${_PASSWORD_R}${_PASSWORD_P}"
        f"${_encode_password_component(salt)}${_encode_password_component(digest)}"
    )


def verify_password(password: str, encoded_hash: str | None) -> bool:
    """使用哈希中记录的参数校验密码，格式异常时安全返回 False。"""
    if not encoded_hash:
        return False
    try:
        scheme, n, r, p, salt_text, digest_text = encoded_hash.split("$")
        if scheme != _PASSWORD_SCHEME:
            return False
        salt = _decode_password_component(salt_text)
        expected = _decode_password_component(digest_text)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError, IndexError):
        return False


def create_access_token(user_id: str, settings: Settings) -> tuple[str, int]:
    """创建包含用户主体、签发时间和过期时间的短时访问令牌。"""
    now = datetime.now(UTC)
    expires_in = settings.access_token_expire_minutes * 60
    payload: dict[str, Any] = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "type": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """校验访问令牌签名、算法、有效期和令牌类型并返回载荷。"""
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
        options={"require": ["sub", "iat", "exp"]},
    )
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("令牌类型无效")
    return payload
