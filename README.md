# Attestor — Day 1

Model-risk governance and evaluation platform for GenAI/agentic models,
built for the SR 26-2 regime.

## Day 1 scope

Repo scaffold, full Docker Compose stack, Postgres schema via Alembic,
Keycloak realm with RBAC, the deterministic materiality scorecard, the
hash-chained audit log, and local document ingestion into Qdrant. No LLM
agent logic yet — that starts Day 2.

## Prerequisites

- Docker Desktop running
- `uv` installed (`brew install uv`)
- An Anthropic API key
- A Groq API key (free tier: https://console.groq.com)

## Setup

```bash
cp .env.example .env
# edit .env: paste in ANTHROPIC_API_KEY and GROQ_API_KEY

chmod +x scripts/bootstrap.sh
./scripts/bootstrap.sh
```

This brings up every container, runs migrations, seeds four Keycloak users
with randomly generated passwords (written to `.env.local`, gitignored),
and registers Attestor itself plus three subject models in the inventory.

## Verify Day 1 is working

Run each of these and paste me the output — this is the Day 1 gate.

**1. All containers healthy:**
```bash
docker compose ps
```
Every service should show `healthy` or `running`.

**2. API is up and can reach its dependencies:**
```bash
curl -s http://localhost:8000/health/ready | python3 -m json.tool
```
Expect `"status": "ready"` with `postgres`, `qdrant`, `redis` all `"ok"`.

**3. Migrations applied cleanly:**
```bash
uv run alembic current
```
Should print `0001 (head)`.

**4. Unit tests pass:**
```bash
uv run pytest tests/unit -v
```

**5. Keycloak users exist and can authenticate.** Get a token for the
model owner (password is in `.env.local`):
```bash
source .env.local
curl -s -X POST http://localhost:8080/realms/attestor/protocol/openid-connect/token \
  -d "grant_type=password" \
  -d "client_id=attestor-api" \
  -d "client_secret=attestor-api-local-dev-secret-change-in-prod" \
  -d "username=owner" \
  -d "password=$ATTESTOR_DEMO_PASSWORD_OWNER" | python3 -m json.tool
```
Should return an `access_token`.

**6. The inventory is seeded and queryable through the real RBAC path.**
Using the `access_token` from step 5:
```bash
TOKEN="paste-access-token-here"
curl -s http://localhost:8000/models -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```
Should list 4 models: `attestor-validation-agent` plus three subject
models, each with a computed `materiality_tier`.

**7. RBAC actually denies what it should.** Try the same call with the
`auditor` token — should succeed (read-only role). Try registering a new
model as `auditor` — should return `403`.

## Project layout

```
app/
  core/       config, database, auth (Keycloak JWT + RBAC)
  models/     SQLAlchemy ORM + Pydantic schemas
  routers/    FastAPI route handlers
  services/   materiality scorecard, audit log, document ingestion
  agents/     LangGraph validation agent (Day 3)
  evals/      RAGAS + custom scorers (Day 2)
  guardrails/ NeMo Guardrails rails + attribution gate (Day 2)
  gateway/    LLM routing, caching, worker (Day 2)
  mcp/        FastMCP read-only server (Day 3)
alembic/      migrations
keycloak/     realm import (roles + client config, no secrets)
scripts/      bootstrap, Keycloak/data seeding
tests/
```

## Known Day 1 limitations (see architecture notes for the full list)

- JWKS keys are cached for process lifetime — a Keycloak key rotation needs
  an API restart to pick up. Fine for local dev, flagged for hardening.
- The audit hash chain detects application-level tampering, not an
  attacker with raw DB write access recomputing the whole chain. Offsite
  anchoring of the chain head is scoped for Day 2.
- No rate-limit tuning beyond a flat per-minute default — per-role quotas
  come with the LLM gateway on Day 2.
