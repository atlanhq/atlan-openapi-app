#!/usr/bin/env python3
"""Post-scrape PII and credential redactor.

Usage:
    uv run python extracts/<connector_name>/scrub.py

Walks all .json and .jsonl files under raw/ and applies regex redactions
in-place on the raw text (not parsed JSON — this catches patterns inside
string values). Redacted values are replaced with a safe placeholder.

Run this after every scrape to ensure no credentials or PII leak into
version control or shared storage. The script is idempotent: re-running on
already-scrubbed files makes no changes (the placeholder strings do not match
the redaction patterns).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Redaction patterns
# ---------------------------------------------------------------------------

# Each entry: (label, compiled_pattern, replacement_string)
# Patterns operate on raw file text (not parsed JSON).
REDACT_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "password_in_querystring",
        re.compile(r"(?i)((?:password|pwd)\s*=\s*)([^;\"'\s,}]+)"),
        r"\1REDACTED",
    ),
    (
        "email_address",
        # Negative lookahead: skip already-redacted placeholder
        re.compile(
            r"(?i)\b(?!redacted@example\.com)"
            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
        ),
        "redacted@example.com",
    ),
]

# ---------------------------------------------------------------------------
# Scrub logic
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
RAW_DIR = SCRIPT_DIR / "raw"


def scrub_file(path: Path) -> dict[str, int]:
    """Scrub a single file in-place. Returns {pattern_label: count} of replacements."""
    text = path.read_text(encoding="utf-8")
    counts: dict[str, int] = {}
    for label, pattern, replacement in REDACT_PATTERNS:
        new_text, n = pattern.subn(replacement, text)
        if n:
            counts[label] = n
            text = new_text
    if counts:
        path.write_text(text, encoding="utf-8")
    return counts


def main() -> None:
    if not RAW_DIR.exists():
        print(f"ERROR: raw/ directory not found at {RAW_DIR}")
        print("Run scrape.py first, then run scrub.py.")
        sys.exit(1)

    total_files = 0
    total_replacements = 0

    for path in sorted(RAW_DIR.rglob("*")):
        if path.suffix not in {".json", ".jsonl"}:
            continue
        counts = scrub_file(path)
        if counts:
            total_files += 1
            file_total = sum(counts.values())
            total_replacements += file_total
            print(f"  {path.relative_to(SCRIPT_DIR)}: {file_total} replacement(s)")
            for label, n in counts.items():
                print(f"    [{label}] {n}")

    if total_replacements == 0:
        print("No PII or credentials found — files are clean.")
    else:
        print(f"\nScrubbed {total_replacements} value(s) across {total_files} file(s).")


if __name__ == "__main__":
    main()
