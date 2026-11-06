r"""Où en est l'objectif de rentabilité du propriétaire (D-031) ? Lecture seule du ledger.

Usage : python scripts\goal_status.py [--db data\paper.db]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memepilot.ledger.ledger import Ledger  # noqa: E402
from memepilot.reporting.weekly import owner_goal  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=str(Path(__file__).resolve().parents[1] / "data" / "paper.db"))
    a = p.parse_args()
    lg = Ledger(a.db)
    try:
        print(owner_goal(lg).line())
    finally:
        lg.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
