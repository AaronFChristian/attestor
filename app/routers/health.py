"""
/health/live = process is up (for container liveness probes).
/health/ready = process AND its dependencies (Postgres, Qdrant, Redis) are
reachable. Distinguishing these matters: a liveness probe that also checks
Postgres will restart-loop the API container every time the database has a
blip, which is worse than the original problem.
"""
from fastapi import APIRouter
from qdrant_client import QdrantClient
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import engine

router = APIRouter(prefix="/health", tags=["health"])
settings = get_settings()


@router.get("/live")
async def live():
    return {"status": "live"}


@router.get("/ready")
async def ready():
    checks = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"error: {exc}"

    try:
        qc = QdrantClient(url=settings.qdrant_url)
        qc.get_collections()
        checks["qdrant"] = "ok"
    except Exception as exc:
        checks["qdrant"] = f"error: {exc}"

    try:
        r = Redis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    return {"status": "ready" if all_ok else "degraded", "checks": checks}
