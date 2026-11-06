"""Compare des règles de sortie sur les prix réellement observés de tes trades papier (D-026).

Usage (PowerShell) :
    python scripts\\replay_exits.py               # base data\\paper.db, grille de variantes par défaut
    python scripts\\replay_exits.py --db data\\paper_20261101_041214.db
Lecture seule : rien n'est modifié. Un petit nombre de trades ne prouve rien (≥ 30 conseillé).
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memepilot.ledger.ledger import Ledger  # noqa: E402
from memepilot.paper.replay import ReplayResult, default_grid, replay_position  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Rejeu des règles de sortie sur les prix enregistrés")
    p.add_argument("--db", default=str(ROOT / "data" / "paper.db"))
    a = p.parse_args()
    if not Path(a.db).exists():
        print(f"Base introuvable : {a.db}")
        return 1
    led = Ledger(a.db)
    positions = led.closed_for_replay()
    with_ticks = [(pos, led.ticks(pos["token"], pos["opened_ts"])) for pos in positions]
    with_ticks = [(pos, t) for pos, t in with_ticks if t]
    actual = sum((pos["pnl_usd"] for pos, _ in with_ticks if pos["pnl_usd"] is not None), Decimal(0))
    print(f"{len(positions)} trade(s) fermé(s), dont {len(with_ticks)} avec prix enregistrés (rejouables).")
    if not with_ticks:
        print("Rien à rejouer pour l'instant : les prix sont enregistrés depuis D-026 (2026-11-01).")
        return 0
    print(f"Résultat RÉEL de ces trades : {actual:+.2f} $\n")
    results = []
    for label, cfg in default_grid():
        trades = [t for t in (replay_position(pos, ticks, cfg) for pos, ticks in with_ticks) if t is not None]
        results.append(ReplayResult(label, trades))
    results.sort(key=lambda r: r.total, reverse=True)
    print(f"{'règle de sortie':34} {'P&L total':>10} {'gagnants':>9} {'durée moy.':>11}")
    for r in results:
        held = sum(t.held_min for t in r.trades) / len(r.trades)
        print(f"{r.label:34} {r.total:>+9.2f}$ {r.wins:>4}/{len(r.trades):<4} {held:>8.1f} min")
    incomplete = sum(1 for t in results[0].trades if not t.complete)
    if incomplete:
        print(f"\n⚠ {incomplete} trade(s) : prix enregistrés interrompus avant la sortie (dernier prix utilisé).")
    if len(with_ticks) < 30:
        print(f"⚠ Seulement {len(with_ticks)} trade(s) : comparaison indicative, pas une preuve (≥ 30 conseillé).")
    led.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
