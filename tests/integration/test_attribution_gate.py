"""
Integration test for the attribution gate — the single most important
control in the system. This runs against a real database (SQLite
in-memory, same technique used to verify the Day 1 enum fix), not mocks,
because the whole point of this gate is that it does real lookups.

This closes a gap flagged at the end of Day 2: these tests didn't exist
yet, and the gate had never been proven against an actual database insert
until now.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.guardrails.attribution import ProposedFinding, RailTripReason, verify_attribution
from app.models.orm import EvidenceRecord, GovernedModel, MaterialityTier, ModelStatus, User, UserRole


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def sample_model(db: AsyncSession) -> GovernedModel:
    owner = User(
        id=uuid.uuid4(),
        keycloak_subject="test-sub",
        email="owner@test.local",
        display_name="Test Owner",
        role=UserRole.MODEL_OWNER,
    )
    db.add(owner)
    await db.flush()

    model = GovernedModel(
        id=uuid.uuid4(),
        name="test-model",
        owner_team="Test Team",
        owner_user_id=owner.id,
        materiality_tier=MaterialityTier.TIER_1,
        materiality_score={},
        status=ModelStatus.REGISTERED,
        model_type="rag",
    )
    db.add(model)
    await db.flush()
    return model


class TestAttributionGate:
    async def test_grounded_finding_with_no_metric_passes(self, db, sample_model):
        evidence = EvidenceRecord(
            id=uuid.uuid4(),
            model_id=sample_model.id,
            evidence_type="document",
            source="test",
            payload={"note": "design doc content"},
        )
        db.add(evidence)
        await db.flush()

        proposed = ProposedFinding(
            model_id=sample_model.id,
            evidence_id=evidence.id,
            pillar="conceptual_soundness",
            claim="No documented rationale for retrieval top-k.",
            severity="medium",
            raised_by="test",
        )
        result = await verify_attribution(db, proposed)
        assert result.passed

    async def test_finding_citing_nonexistent_evidence_is_rejected(self, db, sample_model):
        """The core negative control: an agent citing evidence that was
        never recorded is the signature of a fabricated citation."""
        proposed = ProposedFinding(
            model_id=sample_model.id,
            evidence_id=uuid.uuid4(),  # never inserted
            pillar="outcomes_analysis",
            claim="Faithfulness collapsed to 0.42.",
            severity="high",
            raised_by="test",
        )
        result = await verify_attribution(db, proposed)
        assert not result.passed
        assert result.reason == RailTripReason.EVIDENCE_NOT_FOUND

    async def test_finding_citing_evidence_from_different_model_is_rejected(
        self, db, sample_model
    ):
        other_owner = User(
            id=uuid.uuid4(),
            keycloak_subject="other-sub",
            email="other@test.local",
            display_name="Other",
            role=UserRole.MODEL_OWNER,
        )
        db.add(other_owner)
        await db.flush()

        other_model = GovernedModel(
            id=uuid.uuid4(),
            name="other-model",
            owner_team="Other Team",
            owner_user_id=other_owner.id,
            materiality_tier=MaterialityTier.TIER_3,
            materiality_score={},
            status=ModelStatus.REGISTERED,
            model_type="rag",
        )
        db.add(other_model)
        await db.flush()

        evidence = EvidenceRecord(
            id=uuid.uuid4(),
            model_id=other_model.id,  # belongs to the OTHER model
            evidence_type="eval_run",
            source="test",
            payload={"metrics": {"faithfulness": 0.9}},
        )
        db.add(evidence)
        await db.flush()

        proposed = ProposedFinding(
            model_id=sample_model.id,  # finding is against THIS model
            evidence_id=evidence.id,
            pillar="outcomes_analysis",
            claim="Faithfulness is 0.9.",
            severity="low",
            raised_by="test",
        )
        result = await verify_attribution(db, proposed)
        assert not result.passed
        assert result.reason == RailTripReason.EVIDENCE_WRONG_MODEL

    async def test_correctly_cited_metric_passes(self, db, sample_model):
        evidence = EvidenceRecord(
            id=uuid.uuid4(),
            model_id=sample_model.id,
            evidence_type="eval_run",
            source="test",
            payload={"metrics": {"faithfulness": 0.83}},
        )
        db.add(evidence)
        await db.flush()

        proposed = ProposedFinding(
            model_id=sample_model.id,
            evidence_id=evidence.id,
            pillar="outcomes_analysis",
            claim="Faithfulness declined to 0.83.",
            severity="medium",
            raised_by="test",
            cited_metric_name="faithfulness",
            cited_metric_value=0.83,
        )
        result = await verify_attribution(db, proposed)
        assert result.passed

    async def test_misreported_metric_value_is_rejected(self, db, sample_model):
        """The exact failure mode from the module docstring: the agent
        cites a metric that exists, but misreports its value. This is the
        most dangerous kind of error — the citation LOOKS legitimate."""
        evidence = EvidenceRecord(
            id=uuid.uuid4(),
            model_id=sample_model.id,
            evidence_type="eval_run",
            source="test",
            payload={"metrics": {"faithfulness": 0.83}},
        )
        db.add(evidence)
        await db.flush()

        proposed = ProposedFinding(
            model_id=sample_model.id,
            evidence_id=evidence.id,
            pillar="outcomes_analysis",
            claim="Faithfulness collapsed to 0.42.",  # not what the evidence says
            severity="critical",
            raised_by="test",
            cited_metric_name="faithfulness",
            cited_metric_value=0.42,
        )
        result = await verify_attribution(db, proposed)
        assert not result.passed
        assert result.reason == RailTripReason.METRIC_MISMATCH

    async def test_cited_metric_absent_from_evidence_is_rejected(self, db, sample_model):
        evidence = EvidenceRecord(
            id=uuid.uuid4(),
            model_id=sample_model.id,
            evidence_type="eval_run",
            source="test",
            payload={"metrics": {"tool_correctness": 0.9}},
        )
        db.add(evidence)
        await db.flush()

        proposed = ProposedFinding(
            model_id=sample_model.id,
            evidence_id=evidence.id,
            pillar="outcomes_analysis",
            claim="Faithfulness is low.",
            severity="medium",
            raised_by="test",
            cited_metric_name="faithfulness",  # not in this evidence's metrics
            cited_metric_value=0.5,
        )
        result = await verify_attribution(db, proposed)
        assert not result.passed
        assert result.reason == RailTripReason.METRIC_NOT_IN_EVIDENCE

    async def test_empty_claim_is_rejected(self, db, sample_model):
        evidence = EvidenceRecord(
            id=uuid.uuid4(),
            model_id=sample_model.id,
            evidence_type="document",
            source="test",
            payload={},
        )
        db.add(evidence)
        await db.flush()

        proposed = ProposedFinding(
            model_id=sample_model.id,
            evidence_id=evidence.id,
            pillar="conceptual_soundness",
            claim="   ",
            severity="low",
            raised_by="test",
        )
        result = await verify_attribution(db, proposed)
        assert not result.passed
        assert result.reason == RailTripReason.EMPTY_CLAIM

    async def test_metric_tolerance_allows_rounding(self, db, sample_model):
        """A finding quoting 'faithfulness fell to 0.83' should pass even
        if the stored value is 0.8299999 — that's rounding, not a
        misreport. Confirms METRIC_TOLERANCE is doing its job without
        being so loose it'd pass a real mismatch."""
        evidence = EvidenceRecord(
            id=uuid.uuid4(),
            model_id=sample_model.id,
            evidence_type="eval_run",
            source="test",
            payload={"metrics": {"faithfulness": 0.8299999}},
        )
        db.add(evidence)
        await db.flush()

        proposed = ProposedFinding(
            model_id=sample_model.id,
            evidence_id=evidence.id,
            pillar="outcomes_analysis",
            claim="Faithfulness is 0.83.",
            severity="low",
            raised_by="test",
            cited_metric_name="faithfulness",
            cited_metric_value=0.83,
        )
        result = await verify_attribution(db, proposed)
        assert result.passed
