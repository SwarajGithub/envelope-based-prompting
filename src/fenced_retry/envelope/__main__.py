"""Command line: approve or verify an envelope file.

    python -m fenced_retry.envelope verify envelopes/receipt-extract-v1.json
    python -m fenced_retry.envelope approve envelopes/receipt-extract-v1.json --approver "Name" --note "..."
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fenced_retry.envelope.approval import ApprovalError, approve, verify


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fenced_retry.envelope")
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("envelope", type=Path)
    a = sub.add_parser("approve")
    a.add_argument("envelope", type=Path)
    a.add_argument("--approver", required=True)
    a.add_argument("--note", default="")
    args = parser.parse_args(argv)

    if args.cmd == "verify":
        problems = verify(args.envelope)
        if problems:
            for p in problems:
                print("PROBLEM:", p)
            return 1
        print("verified: envelope matches its approval record")
        return 0
    try:
        record = approve(args.envelope, args.approver, args.note)
    except ApprovalError as exc:
        print("NOT APPROVED:", exc)
        return 1
    print(f"approved {record['envelope_id']} {record['version']} hash {record['envelope_hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
