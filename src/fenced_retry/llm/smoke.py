"""One live call against the local Ollama server (M2 smoke test).

Usage (from src/, venv active, Ollama running):
    python -m fenced_retry.llm.smoke [--store-full-text] [--expected-digest DIGEST]
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

from fenced_retry.llm.client import LLMClient, LLMSettings

ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fenced_retry.llm.smoke")
    parser.add_argument("--store-full-text", action="store_true")
    parser.add_argument("--expected-digest", default=None)
    parser.add_argument("--think", action="store_true")
    parser.add_argument("--db", type=Path, default=ROOT / "runs" / "usage.sqlite")
    args = parser.parse_args(argv)

    settings = LLMSettings(
        store_full_text=args.store_full_text,
        expected_digest=args.expected_digest,
        think=args.think,
        usage_db=args.db,
    )
    client = LLMClient(settings)
    try:
        result = client.chat(
            [{"role": "user", "content": "Reply with the single word OK."}],
            project_name="smoke",
        )
        totals = client.tracker.totals()
    finally:
        client.close()
    print(dataclasses.asdict(result))
    print(f"usage file {args.db}: {totals}")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
