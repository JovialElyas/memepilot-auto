"""Rapport hebdomadaire (SPEC F-42) — calculé UNIQUEMENT depuis le ledger.

Contenu : capital début/fin, P&L net, win rate, nombre de trades, plus gros gain/perte, drawdown max,
frais et glissement, performance par stratégie, alertes de sécurité, transactions échouées.
Ajout spécifique au projet : verdict économique face au coût du plan GMGN Plus (B-05).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from memepilot.ledger.ledger import Ledger
from memepilot.paper.shadow import evaluate_shadows, render_shadows

PLUS_MONTHLY_USD = Decimal("29")
# D-031 : critère de rentabilité fixé par le propriétaire. Sur 30 trades fermés depuis les règles D-029/D-030 :
# GAGNÉ si le P&L net (frais compris) est positif OU si au moins 75 % des trades sont gagnants.
GOAL_TRADES = 30
GOAL_WIN_RATE = 0.75
GOAL_SINCE_TS = 1793565000.0  # 2026-11-01 20:30 UTC (16 h 30 Atlantique) : D-040, règles « 5 min » (avant : D-030)
MIN_TRADES_FOR_VERDICT = 50
ZERO = Decimal(0)


@dataclass(frozen=True)
class StrategyStats:
    trades: int
    wins: int
    pnl_usd: Decimal

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0


@dataclass
class WeeklyReport:
    t0: float
    t1: float
    start_capital: Decimal
    end_capital: Decimal
    net_pnl: Decimal
    trades: int
    wins: int
    largest_win: Decimal
    largest_loss: Decimal
    max_drawdown_usd: Decimal
    max_drawdown_pct: float
    fees_usd: Decimal
    slippage_usd: Decimal
    by_strategy: dict[str, StrategyStats] = field(default_factory=dict)
    exit_reasons: dict[str, int] = field(default_factory=dict)
    decisions: int = 0
    proposals: int = 0
    alerts: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    strict_trades: int = 0
    strict_pnl: Decimal = ZERO
    exploration_trades: int = 0
    exploration_pnl: Decimal = ZERO
    goal: OwnerGoal | None = None
    refusals: str = ""  # D-037 : bilan des refus suivis
    refusals_short: list[str] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades else 0.0

    @property
    def days(self) -> float:
        return max(1e-9, (self.t1 - self.t0) / 86400)

    @property
    def monthly_projection(self) -> Decimal:
        """Projection basée UNIQUEMENT sur les trades que le profil strict aurait pris (D-021)."""
        return (self.strict_pnl / Decimal(str(self.days)) * 30).quantize(Decimal("0.01"))

    def verdict(self) -> str:
        if self.strict_trades < MIN_TRADES_FOR_VERDICT:
            return (f"ÉCHANTILLON INSUFFISANT : {self.strict_trades} trade(s) validés par le profil strict "
                    f"< {MIN_TRADES_FOR_VERDICT}. Aucune conclusion économique possible "
                    "(les trades d'exploration servent à calibrer, pas à juger la rentabilité).")
        if self.monthly_projection > PLUS_MONTHLY_USD:
            return (f"POSITIF : projection {self.monthly_projection} $/mois > coût Plus {PLUS_MONTHLY_USD} $/mois "
                    "(à confirmer sur plusieurs semaines).")
        return (f"NÉGATIF : projection {self.monthly_projection} $/mois ≤ coût Plus {PLUS_MONTHLY_USD} $/mois. "
                "Ne pas payer le plan en l'état.")


@dataclass(frozen=True)
class OwnerGoal:
    trades: int  # positions fermées depuis GOAL_SINCE_TS (une vente partielle compte avec sa position)
    window: int  # trades jugés : les GOAL_TRADES derniers
    net_pnl: Decimal
    wins: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.window if self.window else 0.0

    @property
    def status(self) -> str:
        if self.trades < GOAL_TRADES:
            return "EN COURS"
        return "ATTEINT" if self.net_pnl > 0 or self.win_rate >= GOAL_WIN_RATE else "PAS ATTEINT"

    def line(self) -> str:
        head = {"EN COURS": "⏳", "ATTEINT": "✅", "PAS ATTEINT": "❌"}[self.status]
        if self.status == "EN COURS":
            return (f"{head} Objectif rentabilité : {self.trades}/{GOAL_TRADES} trades depuis les nouvelles règles "
                    f"(pour l'instant {self.net_pnl:+.2f} $, {self.win_rate:.0%} gagnants).")
        return (f"{head} Objectif rentabilité {self.status} sur les {self.window} derniers trades : "
                f"P&L net {self.net_pnl:+.2f} $ (frais compris), {self.win_rate:.0%} gagnants "
                f"(critère : P&L > 0 $ ou ≥ {GOAL_WIN_RATE:.0%} gagnants).")


def owner_goal(ledger: Ledger, since_ts: float = GOAL_SINCE_TS) -> OwnerGoal:
    """Critère du propriétaire (D-031), calculé sur des positions entières (vente partielle refondue)."""
    merged: dict[tuple[str, float], list[Decimal | float]] = {}
    for t in ledger.closed_trades():
        if t.opened_ts < since_ts:
            continue
        cur = merged.setdefault((t.token, t.opened_ts), [ZERO, t.closed_ts])
        cur[0] = Decimal(cur[0]) + t.pnl_usd
        cur[1] = max(float(cur[1]), t.closed_ts)
    ordered = sorted(merged.values(), key=lambda v: float(v[1]))
    last = [Decimal(v[0]) for v in ordered[-GOAL_TRADES:]]
    return OwnerGoal(len(ordered), len(last), sum(last, ZERO), sum(1 for x in last if x > 0))


def _drawdown(series: list[tuple[float, Decimal]]) -> tuple[Decimal, float]:
    peak, worst, worst_pct = None, ZERO, 0.0
    for _, eq in series:
        peak = eq if peak is None or eq > peak else peak
        dd = peak - eq
        if dd > worst:
            worst, worst_pct = dd, float(dd / peak) if peak else 0.0
    return worst, worst_pct


def build_weekly(ledger: Ledger, t0: float, t1: float, initial_capital: Decimal) -> WeeklyReport:
    before = [t for t in ledger.closed_trades(0, t0)]
    start = initial_capital + sum((t.pnl_usd for t in before), ZERO)
    trades = ledger.closed_trades(t0, t1)
    net = sum((t.pnl_usd for t in trades), ZERO)
    by: dict[str, list[Decimal]] = defaultdict(list)
    reasons: dict[str, int] = defaultdict(int)
    for t in trades:
        by[t.strategy].append(t.pnl_usd)
        reasons[t.exit_reason.split(" (")[0]] += 1
    series = ledger.equity_series(t0, t1)
    dd, dd_pct = _drawdown(series)
    alerts = [f"{datetime.fromtimestamp(ts, UTC):%m-%d %H:%M} {k} : {d}"
              for ts, k, d in ledger.events(t0, t1, ("halt", "lock", "reconcile", "duplicate", "limits_changed"))]
    failed = [f"{datetime.fromtimestamp(ts, UTC):%m-%d %H:%M} {side} {tok[:6]}… : {err}"
              for ts, tok, side, err in ledger.failed_orders(t0, t1)]
    return WeeklyReport(
        t0=t0, t1=t1, start_capital=start, end_capital=start + net, net_pnl=net, trades=len(trades),
        wins=sum(1 for t in trades if t.pnl_usd > 0),
        largest_win=max((t.pnl_usd for t in trades), default=ZERO),
        largest_loss=min((t.pnl_usd for t in trades), default=ZERO),
        max_drawdown_usd=dd.quantize(Decimal("0.01")), max_drawdown_pct=dd_pct,
        fees_usd=sum((t.fees_usd for t in trades), ZERO), slippage_usd=ledger.slippage_cost(t0, t1),
        by_strategy={k: StrategyStats(len(v), sum(1 for x in v if x > 0), sum(v, ZERO)) for k, v in by.items()},
        exit_reasons=dict(reasons),
        decisions=ledger.count("decisions", "ts >= ? AND ts < ?", (t0, t1)),
        proposals=ledger.count("decisions", "ts >= ? AND ts < ? AND decision = 'PROPOSE'", (t0, t1)),
        alerts=alerts, failed=failed,
        strict_trades=sum(1 for t in trades if t.strict_ok),
        strict_pnl=sum((t.pnl_usd for t in trades if t.strict_ok), ZERO),
        exploration_trades=sum(1 for t in trades if not t.strict_ok),
        exploration_pnl=sum((t.pnl_usd for t in trades if not t.strict_ok), ZERO),
        goal=owner_goal(ledger),
        refusals=render_shadows(shadows := evaluate_shadows(ledger, t0, t1)),
        refusals_short=[f"{k.kind} ×{k.count} : {k.verdict}" for k in shadows],
    )


def render_markdown(r: WeeklyReport) -> str:
    f = "%Y-%m-%d %H:%M UTC"
    strat = "\n".join(f"| {k} | {v.trades} | {v.win_rate:.0%} | {v.pnl_usd:+.2f} $ |" for k, v in
                      sorted(r.by_strategy.items())) or "| — | 0 | — | — |"
    exits = ", ".join(f"{k} ×{v}" for k, v in sorted(r.exit_reasons.items(), key=lambda x: -x[1])) or "—"
    return f"""# Rapport hebdomadaire MemePilot (PAPIER)

Période : {datetime.fromtimestamp(r.t0, UTC):{f}} → {datetime.fromtimestamp(r.t1, UTC):{f}}

| Indicateur | Valeur |
|---|---|
| Capital début → fin | {r.start_capital:.2f} $ → {r.end_capital:.2f} $ |
| P&L net (frais inclus) | **{r.net_pnl:+.2f} $** |
| Trades fermés | {r.trades} (win rate {r.win_rate:.0%}) |
| Plus gros gain / perte | {r.largest_win:+.2f} $ / {r.largest_loss:+.2f} $ |
| Drawdown max | {r.max_drawdown_usd:.2f} $ ({r.max_drawdown_pct:.1%}) |
| Frais / glissement | {r.fees_usd:.2f} $ / {r.slippage_usd:.2f} $ |
| Décisions journalisées / propositions | {r.decisions} / {r.proposals} |
| Trades validés par le profil strict | {r.strict_trades} ({r.strict_pnl:+.2f} $) |
| Trades d'exploration seulement | {r.exploration_trades} ({r.exploration_pnl:+.2f} $) |
| Projection mensuelle (strict) | {r.monthly_projection:+.2f} $ |

**{r.goal.line() if r.goal else ''}**

**Verdict économique :** {r.verdict()}

## Performance par stratégie
| Stratégie dominante | Trades | Win rate | P&L |
|---|---|---|---|
{strat}

Raisons de sortie : {exits}

## Refus suivis (D-037)
{r.refusals}

## Alertes de sécurité
{chr(10).join('- ' + a for a in r.alerts) or '- aucune'}

## Transactions échouées
{chr(10).join('- ' + x for x in r.failed) or '- aucune'}

_Calculé depuis le ledger. Mode papier : aucun ordre réel. Coûts simulés (hypothèses Q-003)._
"""


def render_telegram(r: WeeklyReport) -> str:
    return (f"📊 MemePilot — semaine (papier)\nP&L net : {r.net_pnl:+.2f} $ · capital {r.end_capital:.2f} $\n"
            f"Trades : {r.trades} · win rate {r.win_rate:.0%} · drawdown {r.max_drawdown_usd:.2f} $\n"
            f"Frais {r.fees_usd:.2f} $ · glissement {r.slippage_usd:.2f} $\n"
            f"Strict : {r.strict_trades} trades ({r.strict_pnl:+.2f} $) · exploration : {r.exploration_trades} "
            f"({r.exploration_pnl:+.2f} $)\n{r.goal.line() + chr(10) if r.goal else ''}"
            + "".join(f"🔎 {x}\n" for x in r.refusals_short) + r.verdict())
