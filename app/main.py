import os

import logfire
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import get_settings
from app.routers import evals, health, models, validation

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

if settings.langsmith_api_key:
    # LangSmith's SDK (and LangGraph's built-in tracing hook) read these
    # from os.environ directly — there's no programmatic configure() call
    # equivalent to Logfire's. Setting them here, gated the same way as
    # Logfire above, is what makes this genuinely optional rather than a
    # hard requirement to have a LangSmith account just to run the app.
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project

app.include_router(health.router)
app.include_router(models.router)
app.include_router(evals.router)
app.include_router(validation.router)


@app.get("/")
async def root():
    return {"service": "attestor", "status": "ok", "env": settings.env}
