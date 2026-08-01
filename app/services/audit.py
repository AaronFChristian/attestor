"""
Append-only, hash-chained audit log.

Every entry's hash covers its own content plus the previous entry's hash.
Editing or deleting any row breaks every hash computed after it, so a
verification pass over the whole table (verify_chain) will catch it.

Documented, honest limitation: this protects against an application-level
actor editing rows through normal channels. It does NOT protect against an
attacker with direct database write access who recomputes the entire chain
after tampering — the hash chain alone cannot detect that. The real control
for that threat is periodic anchoring of the chain head digest to an
external, append-only store (e.g. writing the digest to S3 Object Lock, or
a public transparency log) so a recomputed chain can be caught by comparing
against an anchor the attacker doesn't control. That anchoring job is
scoped for Day 2 — call it out explicitly rather than implying the hash
chain alone is tamper-proof.

This module is the ONLY code path permitted to write to audit_log. Every
router and service that needs to log an action calls record(), never the
ORM directly — that's what makes "every write is audited" an actual
guarantee instead of a convention someone forgets.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orm import AuditLogEntry

GENESIS_HASH = "0" * 64


def _compute_entry_hash(
    prev_hash: str,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    detail: dict,
    created_at_iso: str,
) -> str:
    canonical = json.dumps(
        {
            "prev_hash": prev_hash,
            "actor": actor,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "detail": detail,
            "created_at": created_at_iso,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def record(
    db: AsyncSession,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    detail: dict | None = None,
) -> AuditLogEntry:
    """Append one entry to the audit log. Caller is responsible for
    committing the surrounding transaction — this lets a single request
    (e.g. 'accept finding') write both the domain change and the audit
    entry atomically."""
    detail = detail or {}

    result = await db.execute(
        select(AuditLogEntry).order_by(AuditLogEntry.sequence_number.desc()).limit(1)
    )
    last_entry = result.scalar_one_or_none()
    prev_hash = last_entry.entry_hash if last_entry else GENESIS_HASH

    now = datetime.now(timezone.utc)
    entry_hash = _compute_entry_hash(
        prev_hash, actor, action, resource_type, resource_id, detail, now.isoformat()
    )

    entry = AuditLogEntry(
        id=uuid.uuid4(),
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        created_at=now,
    )
    db.add(entry)
    await db.flush()  # get sequence_number without committing yet
    return entry


async def verify_chain(db: AsyncSession) -> dict:
    """Walk the full chain in sequence order and recompute every hash.
    Returns a report rather than raising, so a broken chain is a finding,
    not a 500 error."""
    result = await db.execute(select(AuditLogEntry).order_by(AuditLogEntry.sequence_number.asc()))
    entries = result.scalars().all()

    expected_prev = GENESIS_HASH
    broken_at: list[int] = []

    for entry in entries:
        recomputed = _compute_entry_hash(
            expected_prev,
            entry.actor,
            entry.action,
            entry.resource_type,
            entry.resource_id,
            entry.detail,
            entry.created_at.isoformat(),
        )
        if entry.prev_hash != expected_prev or entry.entry_hash != recomputed:
            broken_at.append(entry.sequence_number)
        expected_prev = entry.entry_hash

    return {
        "total_entries": len(entries),
        "chain_intact": len(broken_at) == 0,
        "broken_at_sequence_numbers": broken_at,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
