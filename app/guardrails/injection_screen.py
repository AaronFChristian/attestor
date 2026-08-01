"""
Prompt-injection screening for ingested documents.

The gap this closes: conceptual_soundness_node reads EvidenceRecord.payload
— arbitrary text from an ingested document — directly into an LLM prompt.
Anyone who can write an EvidenceRecord (which, today, is any authenticated
write path that touches documents) can attempt to inject instructions
toward the judge model. This is exactly the threat class NeMo Guardrails
was originally scoped to cover; this module is the deliberately-scoped-down
version — pattern-based detection on the ingestion path, not a full rules
engine, matched to the actual size of the current threat surface rather
than the size of the framework.

This is a SCREEN, not a guarantee. Pattern matching catches unsophisticated
and moderately-sophisticated injection attempts; it will not catch a
determined adversary using novel phrasing or encoding tricks. Treat a
"clean" result as "nothing obvious was found," not "this text is safe."
The attribution gate remains the actual hard backstop — even if an
injection succeeds in influencing model output, that output still can't
become a persisted Finding without resolving to real evidence.
"""
import re
from dataclasses import dataclass, field

# Deliberately pattern-level, not semantic — a small, auditable list rather
# than an ML classifier, matched to the actual scale of this threat surface
# today. Expand this list as real attempts are observed; don't pre-build a
# comprehensive taxonomy for a threat that hasn't materialized yet.
INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", "instruction override attempt"),
    (r"disregard\s+(all\s+)?(previous|prior|above)", "instruction override attempt"),
    (r"you\s+are\s+now\s+(a|an)\s+", "role reassignment attempt"),
    (r"system\s*:\s*", "fake system-turn injection"),
    (r"\bnew\s+instructions?\s*:", "instruction injection"),
    (r"forget\s+(everything|all)\s+(you|above)", "instruction override attempt"),
    (r"</?(system|assistant|user)>", "fake conversation-turn markup"),
    (r"reveal\s+(your|the)\s+(system\s+)?prompt", "prompt-extraction attempt"),
]

_COMPILED = [(re.compile(pattern, re.IGNORECASE), label) for pattern, label in INJECTION_PATTERNS]


@dataclass
class ScreenResult:
    flagged: bool
    matches: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.flagged:
            return "no injection patterns matched"
        return f"{len(self.matches)} pattern(s) matched: {', '.join(self.matches)}"


def screen_text(text: str) -> ScreenResult:
    """Run the pattern set against a block of text. Does not mutate or
    strip anything — that's a deliberate choice. Silently stripping
    suspicious text can hide a real attack from the human reviewer who
    should know it was attempted; flagging is more honest than
    "sanitizing" and pretending nothing happened."""
    matches = []
    for pattern, label in _COMPILED:
        if pattern.search(text):
            matches.append(label)
    return ScreenResult(flagged=bool(matches), matches=matches)
