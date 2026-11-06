r"""Bilan des refus (D-037) : les refus du bot ont-ils évité des pertes ou fait manquer des gains ?

Usage : python scripts\review_refusals.py [--days 7] [--db data\paper.db]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memepilot.ledger.ledger import Ledger  # noqa: E402
from memepilot.paper.shadow import evaluate_shadows, render_shadows  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=str(ROOT / "data" / "paper.db"))
    p.add_argument("--days", type=float, default=7.0)
    a = p.parse_args()
    lg = Ledger(a.db)
    try:
        print(render_shadows(evaluate_shadows(lg, time.time() - a.days * 86400)))
    finally:
        lg.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
