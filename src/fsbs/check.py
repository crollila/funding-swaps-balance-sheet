"""Reproducibility check used in CI: compare a fresh headline.json with the committed one.

    python -m fsbs.check committed_headline.json results/headline.json
"""
from __future__ import annotations

import json
import math
import sys


def compare(a: dict, b: dict, rel: float = 1e-6, abs_tol: float = 1e-6) -> list[str]:
    bad = []
    for k in sorted(set(a) | set(b)):
        x, y = a.get(k), b.get(k)
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            if not math.isclose(x, y, rel_tol=rel, abs_tol=abs_tol):
                bad.append(f"{k}: committed {x!r} vs fresh {y!r}")
        elif x != y:
            bad.append(f"{k}: committed {x!r} vs fresh {y!r}")
    return bad


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    with open(argv[0]) as f:
        committed = json.load(f)
    with open(argv[1]) as f:
        fresh = json.load(f)
    bad = compare(committed, fresh)
    for line in bad:
        print("MISMATCH", line)
    print(f"{len(committed)} headline values checked, {len(bad)} mismatches")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
