"""M4.3 Ed25519 Capability 签发、验签和防重放边界。"""

import base64
import hashlib
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import redis.asyncio as redis
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field


class CapabilityError(ValueError):
    """Capability 格式、签名或生命周期校验失败。"""


class CapabilityClaims(BaseModel):
    """绑定一次受管副作用所需的最小 Capability 声明。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="capability.v1", pattern=r"^capability\.v1$")
    jti: UUID
    subject: UUID
    tool: str = Field(min_length=1, max_length=128)
    environment: str = Field(min_length=1, max_length=32)
    preparation_id: UUID
    artifact_digest: str = Field(min_length=64, max_length=64)
    issued_at: int = Field(gt=0)
    expires_at: int = Field(gt=0)


_HEADER = {"alg": "EdDSA", "typ": "capability+jwt", "v": 1}


def _encode_part(value: Mapping[str, Any]) -> str:
    """将 JSON 对象编码为无填充 Base64URL 片段。"""
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_part(value: str) -> dict[str, Any]:
    """解码 Base64URL 片段并确保结果是 JSON 对象。"""
    try:
        padding = "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode((value + padding).encode("ascii")))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapabilityError("Capability 编码无效") from exc
    if not isinstance(decoded, dict):
        raise CapabilityError("Capability 载荷必须是 JSON 对象")
    return decoded


def issue_capability(claims: CapabilityClaims, private_key: Ed25519PrivateKey) -> str:
    """使用 Ed25519 私钥签发绑定主体、工具、环境和制品摘要的短时令牌。"""
    if claims.expires_at <= claims.issued_at:
        raise CapabilityError("Capability 过期时间必须晚于签发时间")
    encoded_header = _encode_part(_HEADER)
    encoded_payload = _encode_part(claims.model_dump(mode="json"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = private_key.sign(signing_input)
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{encoded_header}.{encoded_payload}.{encoded_signature}"


def verify_capability(
    token: str,
    public_key: Ed25519PublicKey,
    *,
    now: int | None = None,
    clock_skew_seconds: int = 30,
) -> CapabilityClaims:
    """验签并检查 Capability 的格式、有效期和少量时钟偏差。"""
    parts = token.split(".")
    if len(parts) != 3:
        raise CapabilityError("Capability 格式无效")
    encoded_header, encoded_payload, encoded_signature = parts
    header = _decode_part(encoded_header)
    if header != _HEADER:
        raise CapabilityError("Capability 算法或版本不受支持")
    try:
        padding = "=" * (-len(encoded_signature) % 4)
        signature = base64.urlsafe_b64decode((encoded_signature + padding).encode("ascii"))
        public_key.verify(signature, f"{encoded_header}.{encoded_payload}".encode("ascii"))
    except (ValueError, UnicodeDecodeError, InvalidSignature) as exc:
        raise CapabilityError("Capability 签名无效") from exc
    try:
        claims = CapabilityClaims.model_validate(_decode_part(encoded_payload))
    except ValueError as exc:
        raise CapabilityError("Capability 声明无效") from exc
    current_time = int(time.time()) if now is None else now
    if claims.expires_at <= current_time:
        raise CapabilityError("Capability 已过期")
    if claims.issued_at > current_time + clock_skew_seconds:
        raise CapabilityError("Capability 签发时间超出允许时钟偏差")
    return claims


def load_private_key(path: str) -> Ed25519PrivateKey:
    """从本地 PEM 文件加载 Capability 私钥，不在异常中返回密钥内容。"""
    try:
        key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise CapabilityError("Capability 私钥加载失败") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise CapabilityError("Capability 私钥类型必须是 Ed25519")
    return key


def load_public_key(path: str) -> Ed25519PublicKey:
    """从本地 PEM 文件加载 Capability 公钥。"""
    try:
        key = serialization.load_pem_public_key(Path(path).read_bytes())
    except (OSError, ValueError, TypeError) as exc:
        raise CapabilityError("Capability 公钥加载失败") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise CapabilityError("Capability 公钥类型必须是 Ed25519")
    return key


class ReplayGuard(Protocol):
    """防重放端口，消费成功后同一令牌摘要只能返回一次。"""

    async def consume_once(self, token: str, ttl_seconds: int) -> bool:
        """消费一次 Capability，返回是否为首次消费。"""
        ...


class RedisReplayGuard:
    """使用 Redis SET NX EX 原子操作实现单次 Capability 消费。"""

    def __init__(self, client: redis.Redis, *, key_prefix: str = "etl-agent:capability") -> None:
        """保存 Redis 客户端和项目级键前缀，不保存 Capability 原文。"""
        self.client = client
        self.key_prefix = key_prefix

    async def consume_once(self, token: str, ttl_seconds: int) -> bool:
        """按令牌摘要执行原子一次性消费，并将重放记录设置为有限 TTL。"""
        if ttl_seconds <= 0:
            raise ValueError("Replay Guard TTL 必须为正数")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        key = f"{self.key_prefix}:{digest}"
        result = await self.client.set(key, "1", nx=True, ex=ttl_seconds)
        return bool(result)
