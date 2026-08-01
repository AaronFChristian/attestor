import logfire
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import get_settings
from app.routers import health, models

settings = get_settings()

limiter = Limiter(key_func=get_remote_address, default_limits=[f"{settings.rate_limit_per_minute}/minute"])

app = FastAPI(
    title="Attestor",
    description="Model-risk governance and evaluation platform for GenAI/agentic models.",
    version="0.1.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# No wildcard origins — enforced again here even though config.py validates
# it, because a misconfigured deploy should fail loudly at two layers, not one.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

if settings.logfire_token:
    logfire.configure(token=settings.logfire_token, service_name="attestor-api")
    logfire.instrument_fastapi(app)

app.include_router(health.router)
app.include_router(models.router)


@app.get("/")
async def root():
    return {"service": "attestor", "status": "ok", "env": settings.env}
