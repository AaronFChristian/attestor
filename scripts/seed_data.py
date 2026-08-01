"""
Run AFTER scripts/seed_keycloak_users.py.

1. Pulls each seeded Keycloak user's subject (sub) claim and creates the
   matching local `users` row — this is what lets foreign keys like
   `governed_models.owner_user_id` resolve.
2. Registers Attestor itself as governed_model #1, is_self_governance=True.
   This is not decorative: it is what lets Attestor produce its own
   outcomes-analysis evidence later (its override log, its judge agreement
   rate) using the exact same schema every other model uses.
3. Registers three subject models for Day 2/3 evals to run against.

Run with: uv run python scripts/seed_data.py
"""
import asyncio
import uuid

import httpx

from app.core.database import AsyncSessionLocal
from app.models.orm import GovernedModel, ModelStatus, User, UserRole
from app.services.materiality import MaterialityInputs, compute_materiality

KEYCLOAK_URL = "http://localhost:8080"
REALM = "attestor"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin_local_dev_only"

KEYCLOAK_USERNAME_TO_ROLE = {
    "owner": UserRole.MODEL_OWNER,
    "validator": UserRole.VALIDATOR,
    "mrm_head": UserRole.MRM_HEAD,
    "auditor": UserRole.AUDITOR,
}


async def fetch_keycloak_subjects() -> dict[str, dict]:
    with httpx.Client(timeout=15.0) as client:
        token_resp = client.post(
            f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": ADMIN_USER,
                "password": ADMIN_PASSWORD,
            },
        )
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]

        subjects = {}
        for username in KEYCLOAK_USERNAME_TO_ROLE:
            resp = client.get(
                f"{KEYCLOAK_URL}/admin/realms/{REALM}/users",
                params={"username": username, "exact": "true"},
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            results = resp.json()
            if not results:
                raise RuntimeError(
                    f"User '{username}' not found in Keycloak. "
                    "Run scripts/seed_keycloak_users.py first."
                )
            subjects[username] = results[0]
        return subjects


async def seed_users(subjects: dict[str, dict]) -> dict[str, User]:
    users: dict[str, User] = {}
    async with AsyncSessionLocal() as db:
        for username, role in KEYCLOAK_USERNAME_TO_ROLE.items():
            kc_user = subjects[username]
            from sqlalchemy import select

            result = await db.execute(select(User).where(User.keycloak_subject == kc_user["id"]))
            user = result.scalar_one_or_none()
            if user is None:
                user = User(
                    id=uuid.uuid4(),
                    keycloak_subject=kc_user["id"],
                    email=kc_user.get("email") or f"{username}@attestor.local",
                    display_name=kc_user.get("firstName", username),
                    role=role,
                )
                db.add(user)
                print(f"  created local user row for '{username}' ({role.value})")
            users[username] = user
        await db.commit()
        for u in users.values():
            await db.refresh(u)
    return users


async def seed_models(users: dict[str, User]) -> None:
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select

        # --- Attestor governing itself ---
        existing = await db.execute(select(GovernedModel).where(GovernedModel.name == "attestor-validation-agent"))
        if existing.scalar_one_or_none() is None:
            self_score = compute_materiality(
                MaterialityInputs(
                    decision_autonomy=3,  # findings are proposed, not auto-actioned
                    financial_exposure=2,
                    customer_impact=1,
                    reversibility=2,
                )
            )
            db.add(
                GovernedModel(
                    id=uuid.uuid4(),
                    name="attestor-validation-agent",
                    description="Attestor's own LangGraph validation agent, governed under its own framework.",
                    owner_team="MRM Platform Engineering",
                    owner_user_id=users["mrm_head"].id,
                    materiality_tier=self_score.tier,
                    materiality_score=self_score.model_dump(mode="json"),
                    status=ModelStatus.REGISTERED,
                    model_type="agent",
                    is_self_governance=True,
                )
            )
            print("  registered Attestor as governed_model #1 (self-governance)")

        # --- Three subject models for Day 2/3 evals ---
        subject_specs = [
            {
                "name": "fraud-triage-agent-v3",
                "description": "Agentic fraud alert triage system with autonomous low-risk closure.",
                "owner_team": "Fraud Engineering",
                "model_type": "agent",
                "inputs": MaterialityInputs(
                    decision_autonomy=4, financial_exposure=4, customer_impact=4, reversibility=3
                ),
            },
            {
                "name": "customer-support-rag",
                "description": "RAG assistant answering account and policy questions from internal docs.",
                "owner_team": "Digital Servicing",
                "model_type": "rag",
                "inputs": MaterialityInputs(
                    decision_autonomy=2, financial_exposure=2, customer_impact=3, reversibility=1
                ),
            },
            {
                "name": "credit-line-adjustment-classifier",
                "description": "Classifier recommending credit line increase/decrease bands for review.",
                "owner_team": "Consumer Credit Risk",
                "model_type": "classifier",
                "inputs": MaterialityInputs(
                    decision_autonomy=2, financial_exposure=5, customer_impact=5, reversibility=3
                ),
            },
        ]

        for spec in subject_specs:
            existing = await db.execute(select(GovernedModel).where(GovernedModel.name == spec["name"]))
            if existing.scalar_one_or_none() is not None:
                continue
            score = compute_materiality(spec["inputs"])
            db.add(
                GovernedModel(
                    id=uuid.uuid4(),
                    name=spec["name"],
                    description=spec["description"],
                    owner_team=spec["owner_team"],
                    owner_user_id=users["owner"].id,
                    materiality_tier=score.tier,
                    materiality_score=score.model_dump(mode="json"),
                    status=ModelStatus.REGISTERED,
                    model_type=spec["model_type"],
                )
            )
            print(f"  registered subject model '{spec['name']}' -> {score.tier.value}")

        await db.commit()


async def main() -> None:
    print("Fetching Keycloak subjects...")
    subjects = await fetch_keycloak_subjects()
    print("Seeding local user rows...")
    users = await seed_users(subjects)
    print("Seeding governed models...")
    await seed_models(users)
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
