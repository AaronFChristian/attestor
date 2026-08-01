#!/usr/bin/env bash
# One-shot Day 1 bring-up. Safe to re-run — every step is idempotent.
set -euo pipefail

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and fill in ANTHROPIC_API_KEY and GROQ_API_KEY first."
  exit 1
fi

echo "== Bringing up infrastructure containers =="
docker compose up -d postgres redis qdrant minio keycloak-db keycloak

echo "== Waiting for Postgres =="
until docker compose exec -T postgres pg_isready -U attestor -d attestor >/dev/null 2>&1; do
  sleep 2
done
echo "Postgres ready."

echo "== Installing local deps for scripts (needs uv) =="
uv sync --extra dev

echo "== Running database migrations =="
uv run alembic upgrade head

echo "== Seeding Keycloak demo users (random passwords -> .env.local) =="
uv run python scripts/seed_keycloak_users.py

echo "== Seeding governed model inventory =="
uv run python scripts/seed_data.py

echo "== Building and starting api + worker =="
docker compose up -d --build api worker

echo ""
echo "Bootstrap complete."
echo "  API:            http://localhost:8000/health/ready"
echo "  Keycloak admin: http://localhost:8080 (admin / admin_local_dev_only)"
echo "  Demo passwords: .env.local"
echo "  Qdrant:         http://localhost:6333/dashboard"
echo "  MinIO console:  http://localhost:9001"
