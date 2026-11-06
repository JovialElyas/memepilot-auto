"""Instantané JSON de l'état du bot papier pour l'interface live (lecture seule).

Fonction pure côté données : elle lit le ledger et l'état de risque, ne modifie rien.
Les montants sont convertis en float uniquement pour l'affichage (les calculs restent en Decimal).
"""

from __future__ import annotations

import time
from collections import deque
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from memepilot.paper.exits import stop_level
from memepilot.risk.guardian import RiskDecision, budgets
from memepilot.strategy.hybrid import Decision

if TYPE_CHECKING:
    from memepilot.engine.cycle import CycleReport
    from memepilot.engine.paper import PaperEngine
    from memepilot.risk.state import RiskState


class EventLog:
    """Journal court en mémoire, affiché dans l'interface (le journal durable reste le ledger)."""

    def __init__(self, size: int = 80) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=size)

    def add(self, kind: str, msg: str, ts: float | None = None) -> None:
        self._items.append({"ts": ts or time.time(), "kind": kind, "msg": msg})

    def items(self) -> list[dict[str, Any]]:
        return list(reversed(self._items))


def _primary(client: Any) -> Any:
    """Client GMGN réel derrière l'aiguillage relais (ou le client lui-même)."""
    return getattr(client, "primary", client)


def _f(x: Decimal | float | None, nd: int = 2) -> float | None:
    return None if x is None else round(float(x), nd)


def _signals(d: Any) -> dict[str, dict[str, float]]:
    return {s.module: {"score": round(s.score, 1), "conf": round(s.confidence, 2), "veto": bool(s.vetoes)}
            for s in d.signals}


def build_snapshot(engine: PaperEngine, state: RiskState | None, cycle: CycleReport | None,
                   verdicts: dict[str, RiskDecision], meta: dict[str, Any], log: EventLog,
                   now: float | None = None, cached: dict[str, Any] | None = None) -> dict[str, Any]:
    """`cached` : dernier cycle complet enregistré (affiché tant qu'aucun nouveau cycle complet n'existe)."""
    now = time.time() if now is None else now
    limits = engine.limits
    ex = engine.exits
    trades = engine.ledger.closed_trades()
    wins = sum(1 for t in trades if t.pnl_usd > 0)

    positions = []
    unrealized = Decimal(0)
    for p in engine.ledger.open_positions():
        last = engine.last_prices.get(p.token)
        pnl = (p.qty * last - p.cost_usd) if last is not None else None
        if pnl is not None:
            unrealized += pnl
        positions.append({
            "symbol": p.symbol, "address": p.token, "strategy": p.strategy,
            "cost": _f(p.cost_usd), "entry": float(p.entry_price), "last": None if last is None else float(last),
            "pnl": _f(pnl), "pnl_pct": None if last is None else round(float(last / p.entry_price - 1) * 100, 1),
            "peak_pct": round(float(p.peak_price / p.entry_price - 1) * 100, 1),
            "stop": float(p.entry_price * (1 - ex.stop_loss)),
            "age_min": round((now - p.opened_ts) / 60, 1), "time_stop_min": ex.time_stop_min,
            "missing": p.missing_cycles,
            "sell_at": None if (lvl := stop_level(p.entry_price, p.peak_price, ex)) is None else float(lvl),
            "sell_at_pct": None if lvl is None else round(float(lvl / p.entry_price - 1) * 100, 1),
            "partial_done": p.partial_done,
            "take_profit": None if ex.take_profit is None else float(p.entry_price * (1 + ex.take_profit)),
        })

    candidates = []
    if cycle is not None:
        by_addr = {c.address: c for c in cycle.scan.survivors}
        for d in sorted(cycle.decisions, key=lambda x: -x.score)[:30]:
            c = by_addr.get(d.address)
            v = verdicts.get(d.address)
            candidates.append({
                "symbol": d.symbol, "address": d.address, "score": round(d.score, 1), "risk": str(d.risk_class),
                "decision": str(d.decision), "propose": d.decision is Decision.PROPOSE, "reason": d.reasons[0],
                "signals": _signals(d),
                "liq": _f(c.liquidity, 0) if c else None, "mcap": _f(c.market_cap, 0) if c else None,
                "chg5m": None if not c or c.chg_5m is None else round(c.chg_5m * 100, 1),
                "chg1h": None if not c or c.chg_1h is None else round(c.chg_1h * 100, 1),
                "age_min": None if not c or c.age_min is None else round(c.age_min),
                "guardian": None if v is None else {"ok": v.approved, "size": _f(v.size_usd),
                                                    "why": v.reasons[0] if v.reasons else ""},
            })

    kpis: dict[str, Any] = {"realized_pnl": _f(engine.ledger.realized_pnl_total()), "unrealized": _f(unrealized),
                            "closed": len(trades), "win_rate": round(wins / len(trades) * 100) if trades else None}
    if state is not None:
        b_day, b_cum = budgets(state, limits)
        kpis.update({
            "capital": _f(state.realized_equity_usd), "start": _f(limits.initial_capital_usd),
            "day_loss": _f(state.daily_loss_usd), "day_limit": _f(limits.daily_loss_limit_usd),
            "cum_loss": _f(state.cumulative_loss_usd), "cum_limit": _f(limits.maximum_cumulative_loss_usd),
            "exposure": _f(state.open_exposure_usd), "budget_day": _f(max(b_day, Decimal(0))),
            "budget_cum": _f(max(b_cum, Decimal(0))),
            "trades_today": state.trades_today, "trades_limit": limits.max_trades_per_day,
            "slots": state.slots_used, "slots_limit": limits.max_concurrent_positions,
            "halted": state.halted, "locked": state.locked, "lock_reason": state.lock_reason,
        })

    scan: dict[str, Any] = {}
    if cycle is not None:
        scan = {"seen": cycle.scan.rows_seen, "survivors": len(cycle.scan.survivors),
                "rejected": cycle.scan.rejected, "proposals": len(cycle.proposals),
                "best": round(max((d.score for d in cycle.decisions), default=0.0), 1),
                "sol_usd": cycle.scan.sol_usd, "feed_trades": cycle.feed_trades, "feed_ok": cycle.feed_ok,
                "rate_limited": cycle.scan.rate_limited,
                "duration_s": round(cycle.finished_at - cycle.scan.started_at, 1),
                "top_rejects": cycle.scan.reject_reasons.most_common(6)}

    data_ts = meta.get("data_cycle_ts")
    if cycle is None and cached is not None:
        candidates, scan, data_ts = cached.get("candidates", []), cached.get("scan", {}), cached.get("ts")

    equity = [[round(ts), _f(v)] for ts, v in engine.ledger.equity_series(now - 2 * 86400)][-300:]
    closed = [{"symbol": t.symbol, "pnl": _f(t.pnl_usd), "reason": t.exit_reason, "profile": t.profile,
               "source": t.data_source,
               "strict_ok": t.strict_ok, "closed_ts": t.closed_ts,
               "held_min": round((t.closed_ts - t.opened_ts) / 60, 1)} for t in reversed(trades[-20:])]

    return {
        "now": now, "meta": {**meta, "data_cycle_ts": data_ts, "from_cache": cycle is None and cached is not None,
                             "threshold": engine.cycle_cfg.hybrid.propose_threshold,
                             "profile": engine.cycle_cfg.hybrid.profile,
                             "frozen_s": round(engine.client.limiter.frozen_for, 1),
                             "sources": engine.client.describe() if hasattr(engine.client, "describe") else None,
                             "gmgn_frozen_s": round(_primary(engine.client).limiter.frozen_for, 1),
                             "gmgn_calls": engine.client.calls},
        "kpis": kpis, "scan": scan, "positions": positions, "candidates": candidates,
        "closed": closed, "equity": equity, "log": log.items(),
        "exits": {"stop_loss_pct": float(ex.stop_loss * 100), "trail_after_pct": float(ex.trail_activation * 100),
                  "trail_dd_pct": float(ex.trail_drawdown * 100), "time_stop_min": ex.time_stop_min,
                  "time_stop_gain_pct": float(ex.time_stop_min_gain * 100), "max_hold_min": ex.max_hold_min,
                  "liq_drop_pct": float(ex.liquidity_drop * 100),
                  "floor_pct": None if ex.profit_floor is None else float(ex.profit_floor * 100),
                  "tight_above_pct": float(ex.tight_above * 100), "tight_dd_pct": float(ex.tight_drawdown * 100),
                  "partial_at_pct": None if ex.partial_at is None else float(ex.partial_at * 100),
                  "partial_frac_pct": float(ex.partial_fraction * 100),
                  "take_profit_pct": None if ex.take_profit is None else float(ex.take_profit * 100)},
    }
