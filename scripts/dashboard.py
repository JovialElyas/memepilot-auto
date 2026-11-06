"""Phase 3 — Interface de test : cycles d'analyse en PAPIER + page HTML auto-rafraîchie.

Usage (PowerShell) :
    python scripts\\dashboard.py --demo                 # clé de démo publique, boucle toutes les 60 s
    python scripts\\dashboard.py --demo --cycles 1      # un seul cycle
    python scripts\\dashboard.py --offline              # sans réseau, à partir des fixtures (démonstration)
Puis ouvrir data\\dashboard.html dans le navigateur (il se recharge seul).
Journal des décisions : data\\decisions.jsonl (matière première de la Phase 5).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from datetime import UTC, datetime  # noqa: E402

from memepilot.config.limits import load_limits  # noqa: E402
from memepilot.engine.cycle import CycleConfig, CycleReport, analyze_cycle, append_journal, guard_proposals  # noqa: E402
from memepilot.engine.dashboard import history_entry, render  # noqa: E402
from memepilot.engine.offline import OfflineClient  # noqa: E402
from memepilot.gmgn.rate_limiter import RateLimiter  # noqa: E402
from memepilot.gmgn.read_client import GmgnReadClient  # noqa: E402
from memepilot.risk.state import roll_day  # noqa: E402
from memepilot.risk.store import RiskStore  # noqa: E402

DEMO_KEY = "gmgn_solbscbaseethmonadtron"


async def main_async(args: argparse.Namespace) -> int:
    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    page = data_dir / "dashboard.html"
    if args.offline:
        client: GmgnReadClient = OfflineClient()
        label = "hors ligne (fixtures)"
    else:
        env = {"GMGN_API_KEY": DEMO_KEY} if args.demo else {}
        client = GmgnReadClient(args.cli, limiter=RateLimiter(args.plan), env_overrides=env)
        label = "démo publique (partagée)" if args.demo else "personnelle"
    limits = load_limits(ROOT / "config" / "limits.yaml")
    store = RiskStore(data_dir / "risk_paper.db")
    history: list[dict[str, Any]] = []
    opened = False
    i = 0
    while args.cycles == 0 or i < args.cycles:
        i += 1
        before = client.calls
        rep: CycleReport = await analyze_cycle(client, CycleConfig())
        history.append(history_entry(rep, client.calls - before))
        today = datetime.now(UTC).date()
        risk = roll_day(store.load_or_init(limits, "paper", today), today)
        store.save(risk, "paper")
        verdicts = guard_proposals(rep, risk, limits, today)
        page.write_text(render(rep, key_label=label, refresh_s=int(args.interval), history=history,
                               risk_state=risk, limits=limits, verdicts=verdicts), encoding="utf-8")
        append_journal(data_dir / "decisions.jsonl", rep)
        best = max((d.score for d in rep.decisions), default=0)
        print(f"[{time.strftime('%H:%M:%S')}] cycle {i} : {len(rep.scan.survivors)} survivants, "
              f"{len(rep.proposals)} proposition(s), meilleur {best:.0f}, {client.calls - before} appels"
              + (" — ⚠ limitation de débit" if rep.scan.rate_limited else ""))
        if not opened and not args.no_browser:
            webbrowser.open(page.as_uri())
            opened = True
        if args.cycles and i >= args.cycles:
            break
        await asyncio.sleep(args.interval)
    print(f"Interface : {page}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Interface de test MemePilot (papier)")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--demo", action="store_true", help="clé de démo publique GMGN")
    src.add_argument("--offline", action="store_true", help="rejouer les fixtures, sans réseau")
    p.add_argument("--cycles", type=int, default=0, help="0 = boucle infinie (Ctrl+C pour arrêter)")
    p.add_argument("--interval", type=float, default=60.0)
    p.add_argument("--plan", default="free", choices=["free", "plus", "pro"])
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--cli", default=os.environ.get("GMGN_CLI_PATH", "gmgn-cli"))
    args = p.parse_args()
    if args.interval < 30:
        p.error("--interval doit être ≥ 30 s (quota partagé de la clé de démo)")
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nArrêt demandé.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
