"""为取消、清理、发布和回滚签发独立的短时 Capability。"""

from time import time
from uuid import UUID, uuid4

from etl_agent.harness.capability import CapabilityClaims, issue_capability, load_private_key


def issue_execution_action_capability(
    *,
    private_key_path: str,
    subject: UUID,
    tool: str,
    environment: str,
    preparation_id: UUID,
    artifact_digest: str,
    ttl_seconds: int,
) -> str:
    """为单个执行动作签发绑定 Preparation 和制品摘要的 Capability。"""
    issued_at = int(time())
    ttl = max(60, min(int(ttl_seconds), 3600))
    return issue_capability(
        CapabilityClaims(
            jti=uuid4(),
            subject=subject,
            tool=tool,
            environment=environment,
            preparation_id=preparation_id,
            artifact_digest=artifact_digest,
            issued_at=issued_at,
            expires_at=issued_at + ttl,
        ),
        load_private_key(private_key_path),
    )
