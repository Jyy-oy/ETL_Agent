"""M4.3 Capability 签名和 Replay Guard 测试。"""

from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from etl_agent.harness.capability import (
    CapabilityClaims,
    CapabilityError,
    RedisReplayGuard,
    issue_capability,
    verify_capability,
)


def _claims() -> CapabilityClaims:
    """构造一份固定时间窗口的测试 Capability 声明。"""
    return CapabilityClaims(
        jti=uuid4(),
        subject=uuid4(),
        tool="seatunnel.submit",
        environment="development",
        preparation_id=uuid4(),
        artifact_digest="a" * 64,
        issued_at=1_000,
        expires_at=1_300,
    )


def test_capability_round_trip_binds_claims() -> None:
    """验证签发后只能由对应公钥验签并还原声明。"""
    private_key = Ed25519PrivateKey.generate()
    token = issue_capability(_claims(), private_key)

    verified = verify_capability(token, private_key.public_key(), now=1_100)

    assert verified.tool == "seatunnel.submit"
    assert verified.artifact_digest == "a" * 64


def test_capability_tamper_and_expiry_are_rejected() -> None:
    """验证修改载荷或超过有效期都不能通过验签。"""
    private_key = Ed25519PrivateKey.generate()
    token = issue_capability(_claims(), private_key)
    header, payload, signature = token.split(".")
    tampered = f"{header}.{payload[:-1]}x.{signature}"

    with pytest.raises(CapabilityError, match="签名无效"):
        verify_capability(tampered, private_key.public_key(), now=1_100)
    with pytest.raises(CapabilityError, match="已过期"):
        verify_capability(token, private_key.public_key(), now=1_300)


class FakeRedis:
    """用内存集合模拟 Redis SET NX EX，验证 Replay Guard 的调用契约。"""

    def __init__(self) -> None:
        """初始化已消费键集合。"""
        self.keys: set[str] = set()

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        """只在键不存在时写入，并检查调用方传入正 TTL。"""
        del value
        assert nx is True
        assert ex > 0
        if key in self.keys:
            return False
        self.keys.add(key)
        return True


@pytest.mark.asyncio
async def test_replay_guard_consumes_token_once() -> None:
    """验证同一 Capability 原文第二次消费会被 Redis 原子条件拒绝。"""
    guard = RedisReplayGuard(FakeRedis())  # type: ignore[arg-type]

    assert await guard.consume_once("capability-token", 300) is True
    assert await guard.consume_once("capability-token", 300) is False
