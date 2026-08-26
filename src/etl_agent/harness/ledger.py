"""Evidence Ledger 的哈希链计算和追加写入边界。"""

import hashlib
import json
from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from etl_agent.infrastructure.models import EvidenceLedgerEvent

GENESIS_HASH = "0" * 64


def canonical_json(payload: Mapping[str, object]) -> str:
    """将账本载荷规范化为稳定 JSON，避免字段顺序造成摘要漂移。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_payload(payload: Mapping[str, object]) -> str:
    """计算不包含敏感原文的账本载荷 SHA-256 摘要。"""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def compute_event_hash(
    *,
    previous_hash: str,
    sequence_number: int,
    event_type: str,
    resource_type: str,
    resource_id: UUID,
    actor_id: UUID | None,
    correlation_id: str,
    payload_digest: str,
) -> str:
    """根据前序哈希和当前事件固定字段计算链上事件哈希。"""
    payload = {
        "actor_id": str(actor_id) if actor_id else None,
        "correlation_id": correlation_id,
        "event_type": event_type,
        "payload_digest": payload_digest,
        "prev_event_hash": previous_hash,
        "resource_id": str(resource_id),
        "resource_type": resource_type,
        "sequence_number": sequence_number,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


async def append_evidence_event(
    session: AsyncSession,
    *,
    project_id: UUID,
    event_type: str,
    resource_type: str,
    resource_id: UUID,
    actor_id: UUID | None,
    correlation_id: str,
    payload: Mapping[str, object],
) -> EvidenceLedgerEvent:
    """锁定项目最后一条证据并追加新的哈希链事件。"""
    previous = await session.scalar(
        select(EvidenceLedgerEvent)
        .where(EvidenceLedgerEvent.project_id == project_id)
        .order_by(desc(EvidenceLedgerEvent.sequence_number))
        .limit(1)
        .with_for_update()
    )
    sequence_number = previous.sequence_number + 1 if previous else 1
    previous_hash = previous.event_hash if previous else GENESIS_HASH
    payload_dict = dict(payload)
    payload_digest = digest_payload(payload_dict)
    event_hash = compute_event_hash(
        previous_hash=previous_hash,
        sequence_number=sequence_number,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload_digest=payload_digest,
    )
    event = EvidenceLedgerEvent(
        project_id=project_id,
        sequence_number=sequence_number,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        actor_id=actor_id,
        correlation_id=correlation_id,
        payload_json=payload_dict,
        payload_digest=payload_digest,
        prev_event_hash=previous_hash,
        event_hash=event_hash,
    )
    session.add(event)
    await session.flush()
    return event
