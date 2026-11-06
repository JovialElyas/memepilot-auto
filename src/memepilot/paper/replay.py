"""Rejeu des règles de sortie sur les prix RÉELLEMENT observés des positions papier (D-026).

Chaque position ouverte enregistre son prix toutes les ~5 s (`price_ticks`). On peut donc rejouer, après coup,
n'importe quelle combinaison de règles (trailing, stop-loss, time-stop…) sur les mêmes trajectoires de prix,
avec le même modèle de coûts, et comparer des chiffres plutôt que des impressions.

Limites, à garder en tête : granularité ~5 s (un pic plus court peut être manqué), entrées inchangées (on ne
rejoue que les sorties), et un échantillon de quelques trades ne prouve rien (≥ 30 conseillé).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from memepilot.paper.broker import PaperBroker
from memepilot.paper.costs import CostModel
from memepilot.paper.exits import CURRENT_EXITS, ExitConfig, PositionView, check_exit

ZERO = Decimal(0)


@dataclass(frozen=True)
class ReplayTrade:
    symbol: str | None
    pnl_usd: Decimal
    reason: str
    held_min: float
    complete: bool  # False : les prix enregistrés s'arrêtent avant une sortie (sortie au dernier prix connu)


@dataclass(frozen=True)
class ReplayResult:
    label: str
    trades: list[ReplayTrade]

    @property
    def total(self) -> Decimal:
        return sum((t.pnl_usd for t in self.trades), ZERO)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl_usd > 0)


def replay_position(pos: dict[str, Any], ticks: list[tuple[float, Decimal | None, Decimal | None]],
                    cfg: ExitConfig, costs: CostModel = CostModel()) -> ReplayTrade | None:  # noqa: B008
    """Rejoue UNE position. None si aucun prix n'a été enregistré (position antérieure à D-026)."""
    if not ticks or pos["entry_price"] is None or pos["qty"] is None:
        return None
    view = PositionView(token=pos["token"], entry_price=pos["entry_price"], entry_liquidity=pos["entry_liquidity"],
                        peak_price=pos["entry_price"], opened_ts=pos["opened_ts"], missing_cycles=0)
    broker = PaperBroker(costs)
    pos = dict(pos)
    banked = ZERO  # gain déjà encaissé par une vente partielle (D-030)
    partial_done = False
    last_price: Decimal | None = None
    last_liq: Decimal | None = None
    last_ts = pos["opened_ts"]
    for ts, price, liq in ticks:
        chk = check_exit(view, price, liq, ts, cfg, partial_done=partial_done)
        last_ts = ts
        if price is not None:
            last_price, last_liq = price, liq
        if chk.reason is not None:
            return _close(pos, broker, price, liq, chk.reason, ts, complete=True, banked=banked)
        if chk.partial:
            frac = cfg.partial_fraction
            part = {**pos, "qty": pos["qty"] * frac, "cost_usd": pos["cost_usd"] * frac,
                    "entry_fee_usd": (pos["entry_fee_usd"] or ZERO) * frac}
            banked += _close(part, broker, price, liq, "partielle", ts, complete=True).pnl_usd
            pos = {**pos, "qty": pos["qty"] - part["qty"], "cost_usd": pos["cost_usd"] - part["cost_usd"],
                   "entry_fee_usd": (pos["entry_fee_usd"] or ZERO) - part["entry_fee_usd"]}
            partial_done = True
        view = replace(view, peak_price=chk.peak_price, missing_cycles=chk.missing_cycles)
    return _close(pos, broker, last_price, last_liq, "fin des prix enregistrés", last_ts, complete=False,
                  banked=banked)


def _close(pos: dict[str, Any], broker: PaperBroker, price: Decimal | None, liq: Decimal | None, reason: str,
           ts: float, complete: bool, banked: Decimal = ZERO) -> ReplayTrade:
    fill = broker.sell(pos["token"], pos["qty"], price, liq)
    pnl = fill.gross_usd - pos["cost_usd"] - (pos["entry_fee_usd"] or ZERO) - fill.fee_usd + banked
    return ReplayTrade(pos.get("symbol"), pnl, reason, round((ts - pos["opened_ts"]) / 60, 1), complete)


def default_grid() -> list[tuple[str, ExitConfig]]:
    """Variantes comparées par défaut : les anciennes règles, l'actuelle (D-035), et des voisines."""
    grid: list[tuple[str, ExitConfig]] = [
        ("ancienne (+50 % / −20 %)", ExitConfig(trail_activation=Decimal("0.50"), trail_drawdown=Decimal("0.20"),
                                                profit_floor=None, partial_at=None, tight_above=Decimal("9"),
                                                take_profit=None)),
        ("avant D-030 (sans plancher, ni moitié à +50 %, ni −7 %)",
         ExitConfig(profit_floor=None, partial_at=None, tight_above=Decimal("9"), take_profit=None)),
        ("avant D-035 (sans prise de profit totale)", ExitConfig(take_profit=None)),
        ("D-035 (prise de profit +12 %, stop −25 %, 30 min)", ExitConfig()),
        ("ACTUELLE (5 min, +8 %, −15 %)", CURRENT_EXITS),
    ]
    for tp in ("0.08", "0.10", "0.15", "0.20", "0.30"):
        grid.append((f"prise de profit +{float(tp):.0%}", ExitConfig(take_profit=Decimal(tp))))
    for tp, sl in (("0.05", "0.15"), ("0.12", "0.15"), ("0.08", "0.10"), ("0.08", "0.25")):
        grid.append((f"5 min, +{float(tp):.0%}, −{float(sl):.0%}",
                     replace(CURRENT_EXITS, take_profit=Decimal(tp), stop_loss=Decimal(sl))))
    for minutes in (10.0, 15.0):
        grid.append((f"{minutes:.0f} min, +8 %, −15 %", replace(CURRENT_EXITS, time_stop_min=minutes,
                                                                 max_hold_min=minutes)))
    return grid
