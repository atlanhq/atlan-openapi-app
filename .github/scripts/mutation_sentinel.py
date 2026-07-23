"""Lane 3 — e2e sentinel drills (BLDX-1562).

Fault-injection drilling for tiers too expensive to mutate exhaustively
(integration/e2e). For each sentinel in tests/mutation_sentinels/manifest.toml:
apply its patch, run the target tier's tests, and require them to FAIL
(the fault must be caught). A sentinel whose tier stays green is a blind
spot — reported as an escape.

This is NOT exhaustive mutation testing; it validates that the slow tiers
would notice a handful of representative real bug classes, at the cost of a
few test runs instead of thousands.

Tier → how the kill-signal is run:
  integration : the `mutation_smoke` subset (deterministic DAG, ~6s), runnable
                anywhere the embedded runtime works — exercised in CI + locally.
  e2e         : the `e2e` suite against a live tenant (needs ATLAN_BASE_URL +
                ATLAN_API_KEY). Skipped (reported "not exercised") when those
                are absent, so this lane only truly runs on the release/e2e job.

Usage:
    python .github/scripts/mutation_sentinel.py [--tier integration|e2e|all]

Advisory: writes a table to $GITHUB_STEP_SUMMARY and stdout. Exits non-zero
only if a sentinel ESCAPES a tier that was actually exercised (a real blind
spot); skipped tiers never fail the run.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SENTINEL_DIR = REPO / "tests" / "mutation_sentinels"

# How to run each tier's kill-signal. Integration uses the mutation_smoke
# subset via the MUTATION_LANE hook in tests/conftest.py.
TIER_CMDS: dict[str, list[str]] = {
    "integration": [
        "python",
        "-m",
        "pytest",
        "tests",
        "-o",
        "addopts=",
        "-p",
        "no:cacheprovider",
        "-q",
    ],
    "e2e": [
        "python",
        "-m",
        "pytest",
        "tests/e2e",
        "-o",
        "addopts=",
        "-p",
        "no:cacheprovider",
        "-q",
    ],
}


def load_sentinels() -> list[dict]:
    with open(SENTINEL_DIR / "manifest.toml", "rb") as fh:
        return tomllib.load(fh)["sentinel"]


def tier_runnable(tier: str) -> bool:
    if tier == "e2e":
        return bool(
            os.environ.get("ATLAN_BASE_URL") and os.environ.get("ATLAN_API_KEY")
        )
    return True


def git(*args: str) -> None:
    subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True)


def run_tier(tier: str) -> int:
    """Run the tier's kill-signal; return pytest's exit code."""
    env = dict(os.environ)
    if tier == "integration":
        env["MUTATION_LANE"] = "integration"
    proc = subprocess.run(
        ["uv", "run", "--all-groups", *TIER_CMDS[tier]],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode


def drill(sentinel: dict) -> str:
    """Apply the sentinel, run its tier, revert. Returns a status string."""
    tier = sentinel["tier"]
    patch = SENTINEL_DIR / f"{sentinel['name']}.patch"
    if not tier_runnable(tier):
        return "skipped"
    git("apply", str(patch))
    try:
        rc = run_tier(tier)
    finally:
        git("apply", "-R", str(patch))
    # Non-zero = tests failed = fault caught = good.
    return "caught" if rc != 0 else "ESCAPED"


def render(rows: list[tuple[dict, str]]) -> str:
    lines = [
        "## Mutation testing — Lane 3 (e2e sentinel drills)",
        "",
        "Each sentinel injects one real bug class; its tier's tests must go red.",
        "",
        "| sentinel | tier | bug class | result |",
        "|---|---|---|---|",
    ]
    icon = {
        "caught": "✅ caught",
        "ESCAPED": "❌ ESCAPED",
        "skipped": "⏭️ not exercised",
    }
    for s, status in rows:
        lines.append(
            f"| `{s['name']}` | {s['tier']} | {s['bug_class']} | {icon[status]} |"
        )
    lines.append("")
    escaped = [s["name"] for s, st in rows if st == "ESCAPED"]
    skipped = [s["name"] for s, st in rows if st == "skipped"]
    if escaped:
        lines.append(
            f"**Blind spots (escaped):** {', '.join(escaped)} — add an assertion to the tier."
        )
    if skipped:
        lines.append(
            f"_Not exercised (tier unavailable — e.g. e2e needs ATLAN_BASE_URL/ATLAN_API_KEY): "
            f"{', '.join(skipped)}._"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", default="all", choices=["integration", "e2e", "all"])
    args = parser.parse_args(argv)

    sentinels = [
        s for s in load_sentinels() if args.tier == "all" or s["tier"] == args.tier
    ]
    rows = [(s, drill(s)) for s in sentinels]

    report = render(rows)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if summary:
        with open(summary, "a") as fh:
            fh.write(report)

    # Fail only on a real blind spot in a tier we actually ran.
    return 1 if any(st == "ESCAPED" for _, st in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
