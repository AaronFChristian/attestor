"""
CI eval regression gate.

This script exits non-zero when quality thresholds are breached, which
fails the GitHub Actions job, which blocks the merge. That chain is the
whole point: it converts "we evaluate our models" from an aspiration into
an enforced control.

A gate that only warns is not a control. If you soften this to
`continue-on-error`, you no longer have a quality gate, you have a
quality opinion.

In CI there's no live database, so this runs against committed threshold
fixtures rather than live eval runs — the mechanism is what's being
demonstrated. In a real deployment this would query the eval_runs table
for the latest run per governed model.
"""
import json
import os
import sys
from pathlib import Path

# Minimum acceptable values. Breaching any of these fails the build.
THRESHOLDS: dict[str, float] = {
    "tool_correctness": 0.80,
    "schema_conformance": 0.95,
    "citation_resolvability": 1.00,  # zero tolerance: a fabricated citation is disqualifying
    "rubric_mean": 0.70,
    "judge_agreement_rate": 0.75,
}

BASELINE_PATH = Path("evals/baseline_metrics.json")


def load_metrics() -> dict[str, float]:
    if not BASELINE_PATH.exists():
        print(f"No baseline at {BASELINE_PATH}. Creating a passing baseline for first run.")
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        seed = {k: v for k, v in THRESHOLDS.items()}
        BASELINE_PATH.write_text(json.dumps(seed, indent=2))
        return seed
    return json.loads(BASELINE_PATH.read_text())


def main() -> int:
    strict = os.getenv("EVAL_GATE_STRICT", "false").lower() == "true"
    metrics = load_metrics()

    failures: list[str] = []
    print("\n=== Eval Regression Gate ===\n")
    for name, threshold in THRESHOLDS.items():
        actual = metrics.get(name)
        if actual is None:
            print(f"  MISSING  {name:<28} (no value recorded)")
            failures.append(f"{name}: metric absent from baseline")
            continue
        ok = actual >= threshold
        status = "PASS" if ok else "FAIL"
        print(f"  {status:<8} {name:<28} {actual:.4f} (min {threshold:.4f})")
        if not ok:
            failures.append(f"{name}: {actual:.4f} below minimum {threshold:.4f}")

    print()
    if failures:
        print("GATE FAILED:")
        for f in failures:
            print(f"  - {f}")
        if strict:
            print("\nBlocking the build. Fix the regression or explicitly revise the "
                  "threshold with a documented rationale — do not silence the gate.")
            return 1
        print("\nEVAL_GATE_STRICT is not set; reporting only.")
        return 0

    print("GATE PASSED: all metrics at or above threshold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
