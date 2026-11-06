"""Rapport de performance du trading papier, calculé depuis le ledger (data\\paper.db).

Usage :
    python scripts\\weekly_report.py                # 7 derniers jours → data\\reports\\ + affichage
    python scripts\\weekly_report.py --days 14
    python scripts\\weekly_report.py --send         # envoie aussi le résumé sur Telegram (si configuré)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memepilot.config.envfile import load_project_env  # noqa: E402
from memepilot.config.limits import load_limits  # noqa: E402
from memepilot.ledger.ledger import Ledger  # noqa: E402
from memepilot.reporting.telegram import notifier_from_env  # noqa: E402
from memepilot.reporting.weekly import build_weekly, render_markdown, render_telegram  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Rapport de performance (papier)")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--db", default=str(ROOT / "data" / "paper.db"))
    p.add_argument("--send", action="store_true")
    a = p.parse_args()
    if not Path(a.db).exists():
        print("Aucune base papier : lance d'abord scripts\\paper_trade.py")
        return 1
    limits = load_limits(ROOT / "config" / "limits.yaml")
    ledger = Ledger(a.db)
    now = datetime.now(UTC)
    rep = build_weekly(ledger, (now - timedelta(days=a.days)).timestamp(), now.timestamp(),
                       limits.initial_capital_usd)
    md = render_markdown(rep)
    out = ROOT / "data" / "reports" / f"report_{now:%Y%m%d_%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"Rapport écrit : {out}")
    if a.send:
        asyncio.run(notifier_from_env(load_project_env(ROOT / ".env")).send(render_telegram(rep)))
    ledger.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
