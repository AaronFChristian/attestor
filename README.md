<div align="center">

# Attestor

### Model-risk governance for GenAI and agentic systems, built for the SR 26-2 regime

*A validation report is only as good as what it can prove.*

[![CI](https://github.com/AaronFChristian/attestor/actions/workflows/ci.yml/badge.svg)](https://github.com/AaronFChristian/attestor/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)
![LangGraph](https://img.shields.io/badge/LangGraph-0.2-1C3C3C)
![Next.js](https://img.shields.io/badge/Next.js-16-black)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

</div>

---

## The problem

Under the SR 26-2 model-risk regime, every GenAI and agentic system a bank
runs needs conceptual soundness review, outcomes analysis, and ongoing
monitoring — with a validation report an examiner can defend. The tools
being built for this problem in 2026 are almost all LLM-as-judge systems.
LLM-as-judge systems hallucinate. A validation agent that writes
*"faithfulness collapsed to 0.42"* is dangerous precisely because it
sounds credible — the number is specific, the claim is plausible, and
nobody manually re-derives it before it lands in a report a regulator
reads.

**Attestor's answer:** an AI validation agent proposes findings, but every
single finding is checked against real, resolvable evidence before it is
ever persisted. A finding with no matching evidence record does not get
written — not filtered out later, not softened, blocked at write time by
a deterministic gate that is not itself an LLM call.

---

## Table of contents

- [Architecture at a glance](#architecture-at-a-glance)
- [The validation pipeline](#the-validation-pipeline)
- [A validation run, end to end](#a-validation-run-end-to-end)
- [Data model](#data-model)
- [Deployment topology](#deployment-topology)
- [Finding & run lifecycle](#finding--run-lifecycle)
- [Key features](#key-features)
- [Tech stack](#tech-stack)
- [Repository structure](#repository-structure)
- [Getting started](#getting-started)
- [Testing](#testing)
- [Security & governance](#security--governance)
- [Honest production-readiness assessment](#honest-production-readiness-assessment)
- [Roadmap](#roadmap)

---

## Architecture at a glance

```mermaid
flowchart TB
    subgraph Channels["1 · Users & Channels"]
        WEB["Next.js dashboard<br/>(Keycloak OIDC)"]
        SWAGGER["REST API<br/>(Swagger)"]
        MCP_CLIENT["Claude Desktop<br/>via MCP"]
    end

    subgraph Orchestration["2 · Orchestration — LangGraph"]
        SUP["Supervisor<br/>(tier-based routing)"]
        PILLARS["3 pillar nodes<br/>(parallel per tier)"]
        CHALLENGE["Challenge node<br/>(adversarial review)"]
        GATE["Attribution Gate<br/>⚠️ deterministic, not an LLM"]
        SUP --> PILLARS --> CHALLENGE --> GATE
    end

    subgraph Guardrails["Guardrails"]
        INJECT["Injection screen<br/>(document ingestion)"]
        RBAC["RBAC + segregation<br/>of duties"]
    end

    subgraph Gateway["LLM Gateway"]
        ROUTE["Model routing +<br/>prompt caching"]
        JUDGE1["Claude Sonnet 5<br/>(primary judge)"]
        JUDGE2["Llama 3.1 / Groq<br/>(secondary judge)"]
        ROUTE --> JUDGE1
        ROUTE --> JUDGE2
    end

    subgraph Data["Data & Evidence"]
        PG[("Postgres<br/>schema + audit log +<br/>LangGraph checkpoints")]
        QDRANT[("Qdrant<br/>vector store")]
    end

    subgraph Observability["Observability"]
        LOGFIRE["Logfire<br/>(request tracing)"]
        LANGSMITH["LangSmith<br/>(agent tracing)"]
    end

    Channels --> Orchestration
    Orchestration --> Guardrails
    Orchestration --> Gateway
    Orchestration --> Data
    Orchestration --> Observability
    MCP_CLIENT -.read-only.-> PG

    style GATE fill:#7f1d1d,color:#fff
    style INJECT fill:#78350f,color:#fff
    style RBAC fill:#78350f,color:#fff
```

**Read this diagram as:** every arrow into the Attribution Gate is a
finding proposal; every arrow out is either a persisted database row or
nothing at all. Nothing about this system lets a proposed finding skip
that gate.

---

## The validation pipeline

This is the actual LangGraph topology in `app/agents/validation_graph.py`
— not a simplified version of it.

```mermaid
flowchart LR
    START([Start]) --> SUP[Supervisor<br/>reads materiality tier]

    SUP -->|tier_1| CS[Conceptual<br/>Soundness]
    SUP -->|tier_1| OA[Outcomes<br/>Analysis]
    SUP -->|tier_1, tier_2, tier_3| OM[Ongoing<br/>Monitoring]

    CS --> CH[Challenge<br/>adversarial review]
    OA --> CH
    OM --> CH

    CH --> GATE{Attribution<br/>Gate}
    GATE -->|evidence resolves| PERSIST[(Finding row<br/>written)]
    GATE -->|no match| DROP[Dropped +<br/>audit logged]

    PERSIST --> PAUSE["⏸ INTERRUPT<br/>real LangGraph checkpoint"]

    PAUSE -.human works the queue.-> REVIEW[Accept / Reject / Amend<br/>+ required rationale]
    REVIEW --> RESUME[Resume]

    RESUME --> FINALIZE[Finalize Report<br/>re-reads DB, not stale state]
    FINALIZE --> SIGNOFF[Sign-off<br/>MRM Head only]
    SIGNOFF --> END([Signed off])

    style GATE fill:#7f1d1d,color:#fff
    style PAUSE fill:#92400e,color:#fff
    style SIGNOFF fill:#581c87,color:#fff
```

**The pause is not cosmetic.** By the time it happens, findings are
already real, persisted, human-reviewable database rows — not in-memory
graph state waiting to be formatted. `finalize_report_node` re-queries
`findings` from Postgres rather than trusting what the graph proposed, so
every accept/reject/amend decision made during the pause is what actually
lands in the final report. Backed by a Postgres-native checkpointer
(`langgraph-checkpoint-postgres`), so a paused run survives an API
restart and is visible to any replica — not held in one process's memory.

---

## A validation run, end to end

```mermaid
sequenceDiagram
    actor V as Validator
    participant API as FastAPI
    participant LG as LangGraph
    participant PG as Postgres
    participant LLM as LLM Gateway

    V->>API: POST /validation-runs
    API->>PG: Segregation-of-duties check<br/>(validator ≠ model owner)
    API->>LG: graph.ainvoke(initial_state)

    par Parallel pillar execution (tier 1)
        LG->>LLM: conceptual_soundness_node
        LG->>PG: outcomes_analysis_node<br/>(deterministic, no LLM call)
        LG->>PG: ongoing_monitoring_node<br/>(drift check)
    end

    LG->>LLM: challenge_node (adversarial review)
    LG->>PG: attribution gate verifies each finding
    PG-->>LG: only grounded findings persisted
    LG-->>API: paused at interrupt_before

    API-->>V: 202 awaiting_review

    V->>API: GET /validation-runs/{id}/findings
    loop Each finding
        V->>API: accept / reject / amend + rationale
        API->>PG: update Finding.status
    end

    V->>API: POST /validation-runs/{id}/finalize
    API->>LG: resume graph
    LG->>PG: re-read findings (not stale state)
    LG->>PG: write validation_report evidence
    LG-->>API: report_evidence_id

    actor M as MRM Head
    M->>API: POST /validation-runs/{id}/sign-off
    API->>PG: status = signed_off, timestamp, signer
```

---

## Data model

```mermaid
erDiagram
    GovernedModel ||--o{ ModelVersion : has
    GovernedModel ||--o{ EvidenceRecord : generates
    GovernedModel ||--o{ Finding : "raised against"
    GovernedModel ||--o{ ValidationRun : "validated in"
    GovernedModel ||--o{ ModelDependency : "depends on"

    EvidenceRecord ||--|| Finding : "cited by (NOT NULL)"
    ValidationRun ||--o{ Finding : contains
    ValidationRun }o--|| User : "initiated by"
    ValidationRun }o--o| User : "signed off by"

    Finding }o--o| User : "reviewed by"

    AuditLog {
        int sequence_number PK "Postgres IDENTITY"
        string prev_hash "hash chain"
        string entry_hash
    }

    Finding {
        uuid id PK
        uuid evidence_id FK "NOT NULL — the whole thesis"
        string pillar
        string severity
        string status "proposed|accepted|amended|rejected"
        text review_rationale
    }

    EvidenceRecord {
        uuid id PK
        string evidence_type "eval_run|document|drift_alert"
        jsonb payload
        timestamp created_at "append-only, never updated"
    }

    ValidationRun {
        uuid id PK
        string langgraph_thread_id
        string status "running|awaiting_review|signed_off"
        uuid report_evidence_id FK
    }
```

**The one constraint that matters most in this schema:**
`Finding.evidence_id` is `NOT NULL`. That single column is the
database-level enforcement of the entire thesis — a finding without
resolvable evidence cannot exist as a row, full stop, independent of
whatever the LLM said.

---

## Deployment topology

```mermaid
flowchart TB
    subgraph Browser
        NEXT["Next.js :3000"]
    end

    subgraph Docker["Docker Compose network"]
        API["api :8000<br/>FastAPI + LangGraph"]
        WORKER["worker<br/>ARQ eval jobs"]
        MCPSRV["mcp-server<br/>isolated deps —<br/>fastmcp conflicts with<br/>pinned FastAPI's Starlette"]

        PG[("postgres :5432<br/>+ checkpoint tables")]
        REDIS[("redis :6379")]
        QDRANT[("qdrant :6333")]
        MINIO[("minio :9000")]
        KC["keycloak :8080"]
        KCDB[("keycloak-db")]
        ADMINER["adminer :8081<br/>dev-only"]
    end

    NEXT -->|OIDC + PKCE| KC
    NEXT -->|Bearer token| API
    API --> PG
    API --> REDIS
    API --> QDRANT
    API -->|psycopg, separate<br/>pool from asyncpg| PG
    WORKER --> PG
    WORKER --> REDIS
    MCPSRV -->|read-only| PG
    KC --> KCDB
    ADMINER --> PG

    style MCPSRV fill:#1e3a5f,color:#fff
```

**Why `mcp-server` is a separate service with its own Dockerfile:**
`fastmcp` requires Starlette ≥1.0; the pinned FastAPI needs Starlette
&lt;0.39. Rather than force one dependency tree to satisfy both — which
would mean freezing `fastmcp` forever or repeatedly upgrading FastAPI
across major versions just for one tool server — it runs isolated,
sharing only the ORM/config source (which has zero FastAPI dependency)
via `PYTHONPATH`, not a package install.

---

## Finding & run lifecycle

```mermaid
stateDiagram-v2
    [*] --> proposed : agent writes finding<br/>(passed attribution gate)

    proposed --> accepted : validator/mrm_head
    proposed --> rejected : validator/mrm_head<br/>→ feeds golden set
    proposed --> amended : validator/mrm_head<br/>+ edited claim

    accepted --> [*]
    amended --> [*]
    rejected --> [*] : becomes a negative<br/>training example

    state "ValidationRun.status" as VR {
        [*] --> running
        running --> awaiting_review : graph paused
        awaiting_review --> awaiting_review : finalize (report drafted,<br/>still needs sign-off)
        awaiting_review --> signed_off : mrm_head only —<br/>never the validator
        signed_off --> [*]
    }
```

A **rejected** finding isn't a discarded one — it's written to
`attestor_self_golden` with `label_source='rejected_finding'`, becoming a
negative example that measures the validation agent's own false-finding
rate. Self-governance as a data pipeline, not a slogan.

---

## Key features

- **Deterministic attribution gate** — every finding's evidence is
  resolved against Postgres and its cited metric value compared to the
  stored eval run *before* the finding is persisted. A database lookup,
  not a second LLM call judging the first.
- **Dual-judge scoring** — Claude (Anthropic) and Llama (Groq) score every
  rubric criterion independently. Disagreement beyond a threshold escalates
  to mandatory human review — the concrete answer to "how do you know your
  judge isn't just agreeing with itself."
- **Real interrupt/resume** — a genuine LangGraph `interrupt_before`
  checkpoint, Postgres-backed, survives process restarts and is visible
  across replicas.
- **Segregation of duties, enforced server-side** — a validator cannot
  validate a model they own; sign-off is `mrm_head`-only, never the person
  who ran the review. Checked in the route handler, not just hidden in the UI.
- **Hash-chained, append-only audit log** — every write recomputes and
  verifies a SHA-256 chain. Documented limitation: detects
  application-level tampering, not an attacker with raw DB write access —
  said plainly rather than overclaimed.
- **Blast-radius lineage** — a recursive CTE walks model dependencies
  backward; a failing shared component auto-opens provisional findings on
  every downstream model.
- **Prompt-injection screening** — ingested document text is screened for
  injection patterns before it reaches an LLM prompt, flagged and
  audit-logged rather than silently stripped.
- **Materiality scoring is deterministic, not an LLM guess** — a fixed,
  weighted scorecard, because a risk tier that determines validation
  scrutiny has to be reproducible and arguable in plain arithmetic.

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| API | FastAPI, Pydantic v2 | |
| Orchestration | LangGraph + `langgraph-checkpoint-postgres` | Postgres-backed, not in-memory — survives restarts, works across replicas |
| Database | Postgres 16 + Alembic | |
| Vector store | Qdrant | |
| Auth | Keycloak (OIDC, PKCE), RBAC + ABAC | |
| LLM providers | Claude (Anthropic), Llama (Groq) | Dual-judge, different model families |
| Observability | Logfire + LangSmith | Request tracing + agent-level tracing |
| Frontend | Next.js 16, React 19, TypeScript, Tailwind 4 | `keycloak-js` official adapter, not hand-rolled OIDC |
| Async jobs | ARQ on Redis | |
| CI | GitHub Actions — lint/type/test, SAST (bandit), dependency audit, secret scan (gitleaks), blocking eval-regression gate | |

---

## Repository structure

```
attestor/
├── app/
│   ├── agents/        # LangGraph nodes, state, graph construction
│   ├── core/           # config, database, auth
│   ├── evals/          # scorers, dual-judge, eval runner
│   ├── gateway/         # LLM gateway, ARQ worker
│   ├── guardrails/       # attribution gate, injection screen
│   ├── models/           # SQLAlchemy ORM + Pydantic schemas
│   ├── routers/            # FastAPI route handlers
│   └── services/            # materiality, audit, lineage, drift
├── frontend/               # Next.js app
├── mcp_server/               # isolated FastMCP server (see deployment topology)
├── alembic/                    # migrations
├── tests/                        # unit + integration
└── .github/workflows/ci.yml        # lint, security, eval gate
```

---

## Getting started

```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY, GROQ_API_KEY

chmod +x scripts/bootstrap.sh
./scripts/bootstrap.sh   # containers, migrations, Keycloak users, seed data

cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Open `http://localhost:3000`. Demo credentials are generated fresh on
every bootstrap and written to `.env.local` at the repo root (gitignored)
— never hardcoded, never committed.

---

## Testing

```bash
uv run pytest tests/unit tests/integration -v
```

Unit tests cover the materiality scorecard, the audit hash chain
(including a simulated tamper-detection scenario), the Jaccard
tool-correctness metric, dual-judge parsing, and the injection screen.
Integration tests run the attribution gate against a real database,
including negative controls: fabricated evidence, cross-model citation,
and misreported metric values are all proven rejected, not just assumed.

---

## Security & governance

| Role | Can | Cannot |
|---|---|---|
| `model_owner` | Register models, view findings against them | Validate a model they own, close a finding |
| `validator` | Trigger runs, review the finding queue | Sign off their own report |
| `mrm_head` | Sign off finalized reports, set thresholds | — |
| `auditor` | Read everything | Write anything |

Every restriction above is enforced in the route handler (`app/core/auth.py`,
`app/routers/validation.py`), not only hidden in the UI.

---

## Honest production-readiness assessment

This is a portfolio project, and the strongest thing it can show is not a
claim of completeness but an accurate map of what's actually proven.

**Fully proven, live, against real infrastructure:** schema, auth, audit
chain, LLM gateway, dual-judge evals, attribution gate (with negative
controls), the LangGraph agent including a real interrupt/resume survived
across an API restart, the full HITL review workflow through the actual
UI including a real sign-off, and a green CI pipeline with a
demonstrably-blocking eval gate.

**Built but never exercised on real data:** blast-radius lineage (no
`model_dependencies` row has ever been inserted), distribution drift
detection (written, never called by any node), the self-governance
feedback loop (rejected findings are correctly collected, nothing yet
*reads* that data to compute the agent's own false-finding rate).

**Named in the original design, not built:** RAG retrieval in the agent
(Qdrant is running, nothing queries it yet — `conceptual_soundness_node`
reads evidence rows directly), NeMo Guardrails, a red-teaming module,
PDF report export.

**The one architectural fact that would have blocked calling this
"production-grade":** the LangGraph checkpointer was originally
`MemorySaver` — in-process memory. Two API replicas behind a load
balancer would have made a paused run invisible to whichever replica
didn't start it. This has been fixed
(`langgraph-checkpoint-postgres`, confirmed working across a live restart),
but the fact that it needed fixing is worth saying plainly rather than
glossing over.

---

## Roadmap

- [ ] Wire Qdrant retrieval into `conceptual_soundness_node`
- [ ] Close the self-governance loop — score the validation agent against
      its own rejected-finding golden set on a schedule
- [ ] Seed real `model_dependencies` and exercise blast-radius for real
- [ ] Findings review workspace: bulk actions, filtering
- [ ] PDF export for signed-off reports

---

<div align="center">

Built by [Aaron Christian](https://github.com/AaronFChristian) ·
[LinkedIn](#) · aaronfc.work@gmail.com

</div>
