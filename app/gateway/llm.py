"""
LLM Gateway.

Every model call in Attestor goes through this module. Nothing calls the
Anthropic or Groq SDK directly. That constraint is what makes these
questions answerable instead of "grep and hope":

  - What did this validation run cost?
  - Which exact model version produced this finding?
  - What prompt version was in effect when this eval ran?
  - Who invoked the model, and when?

SR 26-2 expects versioning of models/prompts/chains and an audit trail of
model usage. You cannot retrofit that. It has to be a chokepoint from day
one, which is what this is.

Two things worth knowing before you edit this file:

1. Model IDs are pinned literals in config.py, never aliases. If a vendor
   rolls a "latest" pointer forward, a signed validation report's evidence
   would silently shift underneath it. That's the exact failure mode an
   examiner hunts for in a governance tool.

2. claude-sonnet-5 REJECTS the temperature parameter entirely — not
   "ignores it", rejects the request. Passing temperature=0 raises. This is
   handled in _build_anthropic_kwargs; do not "fix" it by adding a default.

3. Both public methods carry @traceable (LangSmith). This is deliberately
   a no-op when LANGSMITH_TRACING isn't set — confirmed by testing the
   decorator with no tracing env vars configured before adding it here,
   so this never becomes a hard dependency on having a LangSmith account.
   LangGraph's own node execution is traced automatically once the env
   vars ARE set (standard LangChain-ecosystem behavior) — these decorators
   exist specifically because the raw Anthropic/Groq SDK calls inside
   nodes bypass LangChain's instrumented client and wouldn't otherwise
   show up as nested spans under the graph run.
"""
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import anthropic
import groq
from langsmith import traceable
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings

settings = get_settings()

TaskClass = Literal["extraction", "judgment", "challenge", "drafting", "classification"]

# Task class -> which pinned model handles it. Extraction and classification
# are high-volume and low-judgment, so they go to Haiku. Anything where the
# quality of reasoning IS the product goes to Sonnet.
TASK_ROUTING: dict[str, str] = {
    "extraction": settings.model_extraction,
    "classification": settings.model_extraction,
    "judgment": settings.model_judge_primary,
    "challenge": settings.model_judge_primary,
    "drafting": settings.model_judge_primary,
}

# Approximate USD per million tokens. These are for internal cost
# attribution and trend monitoring, not billing reconciliation — treat the
# numbers as indicative. Update when pricing changes.
PRICING_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "llama-3.1-8b-instant": {"input": 0.00, "output": 0.00},  # Groq free tier
}


@dataclass
class LLMResponse:
    text: str
    model_id: str
    prompt_hash: str
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    estimated_cost_usd: float
    latency_ms: int
    provider: str
    degraded: bool = False  # True if we failed over from the intended model
    raw_content: list[Any] = field(default_factory=list)


def compute_prompt_hash(system: str, user: str) -> str:
    """Stable identifier for a prompt version. Stored on every eval run and
    every finding so 'which prompt produced this?' is answerable months
    later, and so eval idempotency can key on it."""
    canonical = json.dumps({"system": system, "user": user}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _estimate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    pricing = PRICING_PER_MTOK.get(model_id)
    if pricing is None:
        return 0.0
    return round(
        (input_tokens / 1_000_000) * pricing["input"]
        + (output_tokens / 1_000_000) * pricing["output"],
        6,
    )


def _build_anthropic_kwargs(model_id: str, temperature: float | None) -> dict:
    """claude-sonnet-5 rejects `temperature` outright. Anything else accepts
    it. Passing temperature=None to the SDK is not the same as omitting the
    key, so we build the dict conditionally."""
    kwargs: dict[str, Any] = {}
    if model_id.startswith("claude-sonnet-5"):
        return kwargs  # omit entirely
    if temperature is not None:
        kwargs["temperature"] = temperature
    return kwargs


class LLMGateway:
    """Single entry point for model calls.

    `cacheable_context` is passed separately from `user` because Anthropic's
    prompt caching works on a prefix basis: the cached block must come
    first and be byte-identical across calls. The regulatory corpus and a
    model card together run 15-20k tokens and get re-sent on every agent
    node — caching them is roughly a 90% input-cost reduction on the
    validation loop, which is the difference between this being affordable
    to demo and not.
    """

    def __init__(self) -> None:
        self._anthropic = (
            anthropic.Anthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key
            else None
        )
        self._groq = groq.Groq(api_key=settings.groq_api_key) if settings.groq_api_key else None

    @retry(
        retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _call_anthropic(
        self,
        model_id: str,
        system: str,
        user: str,
        cacheable_context: str | None,
        max_tokens: int,
        temperature: float | None,
    ) -> tuple[Any, int]:
        system_blocks: list[dict[str, Any]] = [{"type": "text", "text": system}]
        if cacheable_context:
            # cache_control on the LAST block of the prefix marks everything
            # up to and including it as cacheable.
            system_blocks.append(
                {
                    "type": "text",
                    "text": cacheable_context,
                    "cache_control": {"type": "ephemeral"},
                }
            )

        started = time.perf_counter()
        response = self._anthropic.messages.create(
            model=model_id,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": user}],
            **_build_anthropic_kwargs(model_id, temperature),
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        return response, latency_ms

    def _call_groq(
        self, model_id: str, system: str, user: str, max_tokens: int, temperature: float | None
    ) -> tuple[Any, int]:
        started = time.perf_counter()
        response = self._groq.chat.completions.create(
            model=model_id,
            max_tokens=max_tokens,
            temperature=temperature if temperature is not None else 0.0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        return response, latency_ms

    @traceable(name="llm_gateway_complete", run_type="llm")
    def complete(
        self,
        task_class: TaskClass,
        system: str,
        user: str,
        cacheable_context: str | None = None,
        max_tokens: int = 2048,
        temperature: float | None = 0.0,
        model_override: str | None = None,
    ) -> LLMResponse:
        model_id = model_override or TASK_ROUTING[task_class]
        prompt_hash = compute_prompt_hash(system + (cacheable_context or ""), user)

        if self._anthropic is None:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not configured. Set it in .env — the gateway "
                "will not silently fall back to a different provider for a primary call."
            )

        degraded = False
        try:
            response, latency_ms = self._call_anthropic(
                model_id, system, user, cacheable_context, max_tokens, temperature
            )
        except (anthropic.APIStatusError, anthropic.APIConnectionError):
            # Documented degraded mode: fall back to the cheaper model rather
            # than failing the whole validation run. The `degraded` flag is
            # persisted on the eval run so a report never silently claims
            # Sonnet-quality output that actually came from Haiku.
            if model_id == settings.model_judge_primary:
                degraded = True
                model_id = settings.model_extraction
                response, latency_ms = self._call_anthropic(
                    model_id, system, user, cacheable_context, max_tokens, temperature
                )
            else:
                raise

        text = "".join(block.text for block in response.content if block.type == "text")
        usage = response.usage
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        cached_tokens = getattr(usage, "cache_read_input_tokens", 0) or 0

        return LLMResponse(
            text=text,
            model_id=model_id,
            prompt_hash=prompt_hash,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_tokens,
            estimated_cost_usd=_estimate_cost(model_id, input_tokens, output_tokens),
            latency_ms=latency_ms,
            provider="anthropic",
            degraded=degraded,
            raw_content=list(response.content),
        )

    @traceable(name="llm_gateway_complete_secondary_judge", run_type="llm")
    def complete_secondary_judge(
        self, system: str, user: str, max_tokens: int = 1024, temperature: float | None = 0.0
    ) -> LLMResponse:
        """The independent second judge. Deliberately a different model
        family from the primary — see app/evals/judges.py for why this
        matters (self-preference bias)."""
        if settings.judge_provider == "groq":
            if self._groq is None:
                raise RuntimeError("GROQ_API_KEY not configured but JUDGE_PROVIDER=groq.")
            model_id = settings.model_judge_secondary_groq
            response, latency_ms = self._call_groq(
                model_id, system, user, max_tokens, temperature
            )
            text = response.choices[0].message.content or ""
            usage = response.usage
            return LLMResponse(
                text=text,
                model_id=model_id,
                prompt_hash=compute_prompt_hash(system, user),
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                cached_input_tokens=0,
                estimated_cost_usd=0.0,
                latency_ms=latency_ms,
                provider="groq",
            )

        raise NotImplementedError(
            f"judge_provider='{settings.judge_provider}' is declared in config but has no "
            "adapter implemented yet. Add it here rather than calling an SDK elsewhere — "
            "the gateway is the only place model calls are permitted."
        )


_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
