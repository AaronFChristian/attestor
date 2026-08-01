"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-07-31

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    materiality_tier = postgresql.ENUM(
        "tier_1", "tier_2", "tier_3", name="materialitytier"
    )
    model_status = postgresql.ENUM(
        "registered", "under_validation", "validated", "remediation_required", "retired",
        name="modelstatus",
    )
    finding_severity = postgresql.ENUM(
        "low", "medium", "high", "critical", name="findingseverity"
    )
    finding_status = postgresql.ENUM(
        "proposed", "accepted", "amended", "rejected", "remediated", name="findingstatus"
    )
    user_role = postgresql.ENUM(
        "model_owner", "validator", "mrm_head", "auditor", name="userrole"
    )

    bind = op.get_bind()
    materiality_tier.create(bind, checkfirst=True)
    model_status.create(bind, checkfirst=True)
    finding_severity.create(bind, checkfirst=True)
    finding_status.create(bind, checkfirst=True)
    user_role.create(bind, checkfirst=True)

    # Each enum was just created explicitly above. Without this, the
    # subsequent op.create_table() calls below would ALSO try to auto-create
    # these same enum types as a side effect of creating their columns,
    # colliding with what we just created (DuplicateObjectError). Setting
    # create_type=False tells SQLAlchemy "this type already exists, just
    # reference it" when it's used as a column type from here on.
    materiality_tier.create_type = False
    model_status.create_type = False
    finding_severity.create_type = False
    finding_status.create_type = False
    user_role.create_type = False

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("keycloak_subject", sa.String(100), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200)),
        sa.Column("role", user_role, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "governed_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("owner_team", sa.String(200), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("materiality_tier", materiality_tier, nullable=False),
        sa.Column("materiality_score", postgresql.JSON, nullable=False),
        sa.Column("status", model_status, nullable=False, server_default="registered"),
        sa.Column("model_type", sa.String(100)),
        sa.Column("is_self_governance", sa.Boolean, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "evidence_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("evidence_type", sa.String(50), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("governed_models.id"), nullable=False),
        sa.Column("source", sa.String(200)),
        sa.Column("payload", postgresql.JSON, nullable=False),
        sa.Column("artifact_uri", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_evidence_model_type", "evidence_records", ["model_id", "evidence_type"])

    op.create_table(
        "model_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("governed_models.id"), nullable=False),
        sa.Column("version_label", sa.String(100), nullable=False),
        sa.Column("model_card", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("design_doc_evidence_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("evidence_records.id"), nullable=True),
        sa.Column("prompt_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("model_id", "version_label", name="uq_model_version"),
    )

    op.create_table(
        "model_dependencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("governed_models.id"), nullable=False),
        sa.Column("depends_on_model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("governed_models.id"), nullable=False),
        sa.Column("relationship_type", sa.String(100), server_default="uses_component"),
        sa.UniqueConstraint("model_id", "depends_on_model_id", name="uq_dependency_edge"),
    )

    op.create_table(
        "eval_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("governed_models.id"), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("evidence_records.id"), nullable=False),
        sa.Column("dataset_version", sa.String(100), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("model_id_used", sa.String(100)),
        sa.Column("metrics", postgresql.JSON, nullable=False),
        sa.Column("status", sa.String(30), server_default="completed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("model_id", "dataset_version", "prompt_hash", name="uq_eval_idempotency"),
    )

    op.create_table(
        "validation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("governed_models.id"), nullable=False),
        sa.Column("initiated_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("langgraph_thread_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), server_default="running"),
        sa.Column("report_evidence_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("evidence_records.id"), nullable=True),
        sa.Column("signed_off_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("signed_off_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("governed_models.id"), nullable=False),
        sa.Column("validation_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("validation_runs.id"), nullable=True),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("evidence_records.id"), nullable=False),
        sa.Column("pillar", sa.String(50)),
        sa.Column("claim", sa.Text, nullable=False),
        sa.Column("severity", finding_severity, nullable=False),
        sa.Column("status", finding_status, nullable=False, server_default="proposed"),
        sa.Column("raised_by", sa.String(100)),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("review_rationale", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_findings_model_status", "findings", ["model_id", "status"])

    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sequence_number", sa.Integer, autoincrement=True, nullable=False, unique=True),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100), nullable=False),
        sa.Column("resource_id", sa.String(100), nullable=False),
        sa.Column("detail", postgresql.JSON, server_default="{}"),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "golden_dataset_examples",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_name", sa.String(200), nullable=False),
        sa.Column("dataset_version", sa.String(50), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("governed_models.id"), nullable=True),
        sa.Column("input_payload", postgresql.JSON, nullable=False),
        sa.Column("expected_output", postgresql.JSON, nullable=False),
        sa.Column("label_source", sa.String(50), server_default="human"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_golden_dataset_lookup", "golden_dataset_examples", ["dataset_name", "dataset_version"])


def downgrade() -> None:
    op.drop_table("golden_dataset_examples")
    op.drop_table("audit_log")
    op.drop_index("ix_findings_model_status", table_name="findings")
    op.drop_table("findings")
    op.drop_table("validation_runs")
    op.drop_table("eval_runs")
    op.drop_table("model_dependencies")
    op.drop_table("model_versions")
    op.drop_index("ix_evidence_model_type", table_name="evidence_records")
    op.drop_table("evidence_records")
    op.drop_table("governed_models")
    op.drop_table("users")

    bind = op.get_bind()
    for enum_name in ("userrole", "findingstatus", "findingseverity", "modelstatus", "materialitytier"):
        postgresql.ENUM(name=enum_name).drop(bind, checkfirst=True)
