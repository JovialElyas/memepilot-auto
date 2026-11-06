r"""Bilan du laboratoire (D-041) : quelles stratégies auraient gagné sur les candidats observés ?

Usage : python scripts\lab_report.py [--db data\paper.db]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memepilot.ledger.ledger import Ledger  # noqa: E402
from memepilot.paper.lab import evaluate_lab, render_lab  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=str(ROOT / "data" / "paper.db"))
    a = p.parse_args()
    lg = Ledger(a.db)
    try:
        print(render_lab(*evaluate_lab(lg)))
    finally:
        lg.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
