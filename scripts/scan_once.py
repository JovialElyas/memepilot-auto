"""Phase 2 — Scan en LECTURE SEULE (SHADOW). Aucun ordre, aucune décision d'achat.

Usage :
    python scripts/scan_once.py --demo                      # un cycle avec la clé de démo publique
    python scripts/scan_once.py                             # un cycle avec ta clé
    python scripts/scan_once.py --loop 60 --interval 30     # 60 cycles espacés de 30 s (critère : 30 min sans 429)
Rapports JSON : data/scans/scan_<horodatage>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memepilot.gmgn.rate_limiter import RateLimiter  # noqa: E402
from memepilot.gmgn.read_client import GmgnReadClient  # noqa: E402
from memepilot.scanner.pipeline import ScanConfig, ScanReport, scan_once  # noqa: E402

DEMO_KEY = "gmgn_solbscbaseethmonadtron"


def _fmt(v: float | None, pct: bool = False, money: bool = False) -> str:
    if v is None:
        return "—"
    if pct:
        return f"{v * 100:+.0f}%"
    if money:
        return f"{v / 1000:.0f}k" if v >= 1000 else f"{v:.0f}"
    return f"{v:.2f}"


def print_report(rep: ScanReport) -> None:
    d = rep.to_dict()
    print(f"\n── Scan {time.strftime('%H:%M:%S', time.localtime(rep.started_at))} "
          f"({d['duration_s']} s, {d['calls']} appels, latence moy. {d['avg_latency_ms']} ms)")
    print(f"   SOL = {d['sol_usd']} $ · lignes vues {d['rows_seen']} · rejetées {d['rejected']} "
          f"· survivants {len(rep.survivors)}")
    if d["reject_reasons"]:
        top = ", ".join(f"{k}×{v}" for k, v in list(d["reject_reasons"].items())[:8])
        print(f"   Motifs de rejet : {top}")
    for c in rep.survivors:
        print(f"   ✔ {(c.symbol or '?')[:12]:12} {c.address[:6]}…  liq {_fmt(c.liquidity, money=True):>5}  "
              f"rug {_fmt(c.rug_ratio)}  top10 {_fmt(c.top10)}  SM {c.smart_degen or 0}+{c.renowned or 0}  "
              f"5m {_fmt(c.chg_5m, pct=True)}  1h {_fmt(c.chg_1h, pct=True)}  "
              f"{'[enrichi]' if c.enriched else ''} {'⚠ ' + '; '.join(c.warnings) if c.warnings else ''}")
    for e in d["source_errors"]:
        print(f"   ✘ {e}")
    if rep.parse_errors:
        print(f"   ⚠ {len(rep.parse_errors)} ligne(s) invalide(s) écartée(s)")
    print("   Décision : AUCUNE (lecture seule)")


async def main_async(args: argparse.Namespace) -> int:
    env = {"GMGN_API_KEY": DEMO_KEY} if args.demo else {}
    client = GmgnReadClient(args.cli, limiter=RateLimiter(args.plan), env_overrides=env)
    out_dir = ROOT / "data" / "scans"
    out_dir.mkdir(parents=True, exist_ok=True)
    rate_limited_cycles = 0
    for i in range(args.loop):
        rep = await scan_once(client, ScanConfig())
        print_report(rep)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(rep.started_at))
        (out_dir / f"scan_{stamp}.json").write_text(
            json.dumps(rep.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        if rep.rate_limited:
            rate_limited_cycles += 1
        if i < args.loop - 1:
            await asyncio.sleep(args.interval)
    if args.loop > 1:
        print(f"\nBilan : {args.loop} cycles, {rate_limited_cycles} avec limitation de débit, "
              f"{client.calls} appels GMGN.")
    return 1 if rate_limited_cycles else 0


def main() -> int:
    p = argparse.ArgumentParser(description="Scan GMGN en lecture seule (Phase 2)")
    p.add_argument("--demo", action="store_true", help="clé de démo publique (partagée, souvent limitée)")
    p.add_argument("--loop", type=int, default=1)
    p.add_argument("--interval", type=float, default=30.0)
    p.add_argument("--plan", default="free", choices=["free", "plus", "pro"])
    p.add_argument("--cli", default=os.environ.get("GMGN_CLI_PATH", "gmgn-cli"))
    args = p.parse_args()
    if args.interval < 10:
        p.error("--interval doit être ≥ 10 s")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
