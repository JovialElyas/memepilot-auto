"""Administration MANUELLE du Risk Guardian (hors processus du bot).

Usage :
    python scripts\\risk_admin.py status [--mode paper|live]
    python scripts\\risk_admin.py halt   --reason "texte"     # arrêt d'urgence : plus aucun achat
    python scripts\\risk_admin.py resume --reason "texte"
    python scripts\\risk_admin.py unlock --note "justification"   # demande la phrase de confirmation

Le LOCK (perte cumulée maximale atteinte) ne peut être levé QUE par ce script, avec une phrase
de confirmation tapée à la main. Le déverrouillage ne remet pas les pertes à zéro.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memepilot.config.limits import load_limits  # noqa: E402
from memepilot.risk.guardian import budgets  # noqa: E402
from memepilot.risk.state import set_halt  # noqa: E402
from memepilot.risk.store import UNLOCK_PHRASE, RiskStore, UnlockRefused  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Administration manuelle du risque")
    p.add_argument("action", choices=["status", "halt", "resume", "unlock"])
    p.add_argument("--mode", default="paper", choices=["paper", "live"])
    p.add_argument("--db", default=None)
    p.add_argument("--reason", default="")
    p.add_argument("--note", default="")
    a = p.parse_args()
    limits = load_limits(ROOT / "config" / "limits.yaml")
    # Le bot papier (paper_trade.py) garde son état de risque dans data/paper.db : c'est lui qu'on pilote.
    default_db = ROOT / "data" / ("paper.db" if a.mode == "paper" else "live.db")
    store = RiskStore(a.db or default_db)
    s = store.load_or_init(limits, a.mode, datetime.now(UTC).date())

    if a.action == "halt":
        s = set_halt(s, True, a.reason or "manuel")
        store.save(s, a.mode, event=("halt", a.reason or "manuel"))
    elif a.action == "resume":
        s = set_halt(s, False, a.reason or "manuel")
        store.save(s, a.mode, event=("resume", a.reason or "manuel"))
    elif a.action == "unlock":
        if not s.locked:
            print("Aucun LOCK actif.")
            return 0
        print(f"LOCK actif : {s.lock_reason}")
        print(f"Pour lever le verrou, tape exactement :\n  {UNLOCK_PHRASE}")
        try:
            s = store.admin_unlock(a.mode, input("> "), a.note)
        except UnlockRefused as exc:
            print(f"Refusé : {exc}")
            return 1
        print("Verrou levé. Les pertes cumulées NE sont PAS remises à zéro.")

    b_day, b_cum = budgets(s, limits)
    print(f"Mode {a.mode} · jour {s.day} · LOCK {'OUI' if s.locked else 'non'} · HALT {'OUI' if s.halted else 'non'}")
    print(f"Capital réalisé {s.realized_equity_usd} $ (plus bas {s.realized_equity_low_usd} $)")
    print(f"Pertes : jour {s.daily_loss_usd} $ / {limits.daily_loss_limit_usd} $ · cumul {s.cumulative_loss_usd} $ / "
          f"{limits.maximum_cumulative_loss_usd} $")
    print(f"Exposition ouverte {s.open_exposure_usd} $ · budget jour {b_day} $ · budget cumul {b_cum} $")
    print(f"Positions {len(s.positions)} · ordres en attente {len(s.pending)} · trades aujourd'hui {s.trades_today}")
    for ts, kind, detail in store.events(5):
        print(f"  {datetime.fromtimestamp(ts):%Y-%m-%d %H:%M} {kind}: {detail}")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
