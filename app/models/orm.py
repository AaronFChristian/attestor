"""
Core schema.

Design decisions worth knowing before you touch this file:

1. `findings.evidence_id` is NOT NULL, not nullable. This is the single most
   important constraint in the system. It is the database-level enforcement
   of "no finding without evidence" — the same rule the Pydantic layer and
   the guardrail attribution gate also enforce, so a finding is grounded at
   three independent layers, not one.

2. `evidence_records` rows are never updated, only inserted. There is no
   UPDATE path in the service layer for this table. Treat it as append-only
   even though Postgres doesn't enforce immutability natively (see the
   audit_log hash chain, which is where actual tamper-evidence lives).

3. `model_dependencies` is a self-referencing edge table, queried with a
   recursive CTE for blast-radius propagation (see services/lineage.py on
   Day 3) rather than a graph database — deliberate tradeoff, documented in
   the architecture notes, to avoid a second stateful container.
"""
import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class MaterialityTier(str, PyEnum):
    TIER_1 = "tier_1"  # highest materiality — full pillar sweep mandatory
    TIER_2 = "tier_2"  # moderate — conceptual soundness + monitoring
    TIER_3 = "tier_3"  # low — monitoring only


class ModelStatus(str, PyEnum):
    REGISTERED = "registered"
    UNDER_VALIDATION = "under_validation"
    VALIDATED = "validated"
    REMEDIATION_REQUIRED = "remediation_required"
    RETIRED = "retired"


class FindingSeverity(str, PyEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingStatus(str, PyEnum):
    PROPOSED = "proposed"       # written by the validation agent, not yet reviewed
    ACCEPTED = "accepted"       # human validator accepted as-is
    AMENDED = "amended"         # human validator edited before accepting
    REJECTED = "rejected"       # human validator rejected — feeds the golden set
    REMEDIATED = "remediated"


class UserRole(str, PyEnum):
    MODEL_OWNER = "model_owner"
    VALIDATOR = "validator"
    MRM_HEAD = "mrm_head"
    AUDITOR = "auditor"


class GovernedModel(Base):
    """A model or agentic system under Attestor's governance. Attestor
    registers itself as row #1 — see scripts/seed_data.py."""

    __tablename__ = "governed_models"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    owner_team: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    materiality_tier: Mapped[MaterialityTier] = mapped_column(
        Enum(MaterialityTier, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False
    )
    materiality_score: Mapped[dict] = mapped_column(
        JSON, nullable=False
    )  # raw scorecard inputs + computed score, for reproducibility
    status: Mapped[ModelStatus] = mapped_column(
        Enum(ModelStatus, values_callable=lambda obj: [e.value for e in obj]),
        default=ModelStatus.REGISTERED,
        nullable=False
    )
    model_type: Mapped[str] = mapped_column(String(100))  # rag | agent | classifier | llm_app
    is_self_governance: Mapped[bool] = mapped_column(default=False)  # True only for Attestor itself
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    versions: Mapped[list["ModelVersion"]] = relationship(back_populates="model")


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("model_id", "version_label", name="uq_model_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("governed_models.id"), nullable=False
    )
    version_label: Mapped[str] = mapped_column(String(100), nullable=False)
    model_card: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    design_doc_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_records.id"), nullable=True
    )
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    model: Mapped["GovernedModel"] = relationship(back_populates="versions")


class ModelDependency(Base):
    """Directed edge: model depends on dependency. Walked with a recursive
    CTE for blast-radius propagation."""

    __tablename__ = "model_dependencies"
    __table_args__ = (
        UniqueConstraint("model_id", "depends_on_model_id", name="uq_dependency_edge"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("governed_models.id"), nullable=False
    )
    depends_on_model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("governed_models.id"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(String(100), default="uses_component")


class EvidenceRecord(Base):
    """Append-only. Every eval run, trace sample, red-team result, or ingested
    document becomes one of these. A finding cites this table by id, never
    by inline text — that indirection is what makes a finding checkable."""

    __tablename__ = "evidence_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    evidence_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # eval_run | trace_sample | red_team_result | document | drift_alert
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("governed_models.id"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(200))  # e.g. "ragas_suite", "langsmith_trace"
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)  # structured summary
    artifact_uri: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )  # pointer into MinIO for the raw artifact
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_evidence_model_type", "model_id", "evidence_type"),)


class EvalRun(Base):
    """A single execution of a golden-set eval suite. Idempotency key is
    (model_id, dataset_version, prompt_hash) — same triple returns the
    cached run rather than re-billing tokens."""

    __tablename__ = "eval_runs"
    __table_args__ = (
        UniqueConstraint(
            "model_id", "dataset_version", "prompt_hash", name="uq_eval_idempotency"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("governed_models.id"), nullable=False
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_records.id"), nullable=False
    )
    dataset_version: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id_used: Mapped[str] = mapped_column(String(100))  # pinned LLM id used to run the eval
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)  # {faithfulness: 0.91, ...}
    status: Mapped[str] = mapped_column(String(30), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Finding(Base):
    """A validation finding. evidence_id is NOT NULL — see module docstring.
    Never write to this table directly from an LLM response; it must pass
    through the attribution gate first (app/guardrails/attribution.py)."""

    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("governed_models.id"), nullable=False
    )
    validation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("validation_runs.id"), nullable=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_records.id"), nullable=False
    )
    pillar: Mapped[str] = mapped_column(
        String(50)
    )  # conceptual_soundness | outcomes_analysis | ongoing_monitoring
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[FindingSeverity] = mapped_column(
        Enum(FindingSeverity, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    status: Mapped[FindingStatus] = mapped_column(
        Enum(FindingStatus, values_callable=lambda obj: [e.value for e in obj]),
        default=FindingStatus.PROPOSED,
        nullable=False
    )
    raised_by: Mapped[str] = mapped_column(String(100))  # agent name, e.g. "outcomes_analysis_agent"
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    review_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_findings_model_status", "model_id", "status"),)


class ValidationRun(Base):
    __tablename__ = "validation_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("governed_models.id"), nullable=False
    )
    initiated_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    langgraph_thread_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="running"
    )  # running | awaiting_review | signed_off | rejected
    report_evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_records.id"), nullable=True
    )
    signed_off_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    signed_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    """Mirrors the Keycloak subject. Keycloak is the identity source of
    truth; this row exists for foreign-key relationships and role caching."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    keycloak_subject: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLogEntry(Base):
    """Append-only, hash-chained. Each row's hash covers its own payload plus
    the previous row's hash, so any retroactive edit breaks every hash after
    it. Documented limitation: this detects tampering by an application-level
    actor, not an attacker with full DB write access who recomputes the whole
    chain — the real control for that threat is periodic offsite anchoring of
    the chain head digest (see services/audit.py, Day 2)."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    sequence_number: Mapped[int] = mapped_column(nullable=False, unique=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)  # user email or "system"
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GoldenDatasetExample(Base):
    """Labeled examples used both to eval governed models AND to eval
    Attestor's own validation agent (see finding.status == rejected feeding
    back in here on Day 3)."""

    __tablename__ = "golden_dataset_examples"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    dataset_name: Mapped[str] = mapped_column(String(200), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("governed_models.id"), nullable=True
    )
    input_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    expected_output: Mapped[dict] = mapped_column(JSON, nullable=False)
    label_source: Mapped[str] = mapped_column(
        String(50), default="human"
    )  # human | rejected_finding
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_golden_dataset_lookup", "dataset_name", "dataset_version"),
    )
