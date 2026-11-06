"""Laboratoire (D-041) : tester des stratégies sur TOUS les candidats observés, sans acheter.

Chaque candidat est « acheté » virtuellement au PREMIER prix relevé après la décision (~5 s plus tard, comme la
confirmation d'achat réelle), avec 5 $ et les frais, puis revendu avec les règles de sortie actuelles (5 min,
+8 %, −15 %). Les données sont coupées en deux moitiés dans le temps : une hypothèse n'est VALIDÉE que si elle
gagne de l'argent sur les DEUX moitiés, avec assez de cas dans chacune (sinon c'est peut-être le hasard).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from memepilot.ledger.ledger import Ledger
from memepilot.paper.costs import CostModel
from memepilot.paper.exits import CURRENT_EXITS, ExitConfig
from memepilot.paper.replay import replay_position
from memepilot.paper.shadow import LAB_KIND

ZERO = Decimal(0)
MIN_PER_HALF = 30
STAKE = Decimal("5")


def _sig(f: dict[str, Any], module: str) -> float | None:
    s = (f.get("signals") or {}).get(module) or {}
    return None if not s.get("confidence") else s.get("score")


def _cand(f: dict[str, Any], key: str) -> float | None:
    v = (f.get("candidate") or {}).get(key)
    return None if v is None else float(v)


Rule = Callable[[dict[str, Any]], bool]

# D-043 : H2 « trend follower » (style CTA) — peu de gagnants mais gros : stop −20 %, puis une fois +20 % atteint,
# vente à −25 % sous le sommet ; durée max 4 h ; pas de vente à +8 %, pas de time-stop court.
H2_EXITS = ExitConfig(stop_loss=Decimal("0.20"), trail_activation=Decimal("0.20"), trail_drawdown=Decimal("0.25"),
                      tight_above=Decimal("99"), profit_floor=None, partial_at=None, take_profit=None,
                      time_stop_min=240.0, max_hold_min=240.0)


def _h1(f: dict[str, Any]) -> bool:
    return (_sig(f, "momentum") or 0) >= 80


def _h2(f: dict[str, Any]) -> bool:
    return (_sig(f, "momentum") or 0) >= 60 and (_cand(f, "age_min") or 0) >= 360


# Hypothèses ENREGISTRÉES AVANT les données : H1 (2026-11-02 6 h), H2 (2026-11-02 19 h 30). Les autres = repères.
HYPOTHESES: list[tuple[str, Rule, ExitConfig]] = [
    ("Tous les candidats (repère, sortie 5 min)", lambda f: True, CURRENT_EXITS),
    ("Règle actuelle : PROPOSE (repère, sortie 5 min)", lambda f: f.get("decision") == "PROPOSE", CURRENT_EXITS),
    ("H1 : momentum ≥ 80 (sortie 5 min)", _h1, CURRENT_EXITS),
    ("H1 + liquidité < 50 000 $ (sortie 5 min)", lambda f: _h1(f) and (_cand(f, "liquidity") or 1e12) < 5e4,
     CURRENT_EXITS),
    ("H2 : momentum ≥ 60 et token de 6 h ou plus (trend follower, 4 h)", _h2, H2_EXITS),
    ("Tous les candidats (repère, sortie trend follower)", lambda f: True, H2_EXITS),
]


@dataclass
class HalfStats:
    n: int = 0
    pnl: Decimal = ZERO
    wins: int = 0

    @property
    def avg(self) -> Decimal:
        return self.pnl / self.n if self.n else ZERO


@dataclass
class HypothesisResult:
    name: str
    halves: tuple[HalfStats, HalfStats]

    @property
    def verdict(self) -> str:
        a, b = self.halves
        if min(a.n, b.n) < MIN_PER_HALF:
            return f"EN COURS (il faut ≥ {MIN_PER_HALF} cas par moitié)"
        if a.pnl > 0 and b.pnl > 0:
            return "VALIDÉE : rentable sur les deux moitiés"
        return "REJETÉE : pas rentable sur les deux moitiés"


GLITCH_RATIO = Decimal(3)


GLITCH_LOOKAHEAD = 24  # ≈ 2 min de relevés


def drop_glitches(ticks: list[tuple[float, Decimal | None, Decimal | None]]
                  ) -> list[tuple[float, Decimal | None, Decimal | None]]:
    """Retire les prix aberrants TEMPORAIRES : un saut ×3 (ou ÷3) qui revient au prix d'avant en moins de 2 min.

    Cas réels 2026-11-02 : WWIII affiché ×42 pendant 30 s (DexScreener a basculé sur une autre pair, liquidité
    12 k$ → 298 k$), puis retour au prix normal → +200 $ virtuels qui faussaient tout le bilan. Une vraie
    chute (rug) ou une vraie envolée qui ne reviennent pas sont conservées.
    """
    if not ticks:
        return []
    kept = [ticks[0]]
    i = 1
    while i < len(ticks):
        p, ref = ticks[i][1], kept[-1][1]
        if p and ref and (p > ref * GLITCH_RATIO or p * GLITCH_RATIO < ref):
            lo, hi = ref / Decimal("1.5"), ref * Decimal("1.5")
            back = next((j for j in range(i + 1, min(len(ticks), i + 1 + GLITCH_LOOKAHEAD))
                         if (q := ticks[j][1]) is not None and lo <= q <= hi), None)
            if back is not None:
                i = back  # tout le segment aberrant est ignoré
                continue
        kept.append(ticks[i])
        i += 1
    return kept


def lab_trades(ledger: Ledger, cfg: ExitConfig = CURRENT_EXITS,  # noqa: B008
               costs: CostModel = CostModel(), rule: Rule | None = None,  # noqa: B008
               ) -> list[tuple[float, dict[str, Any], Decimal]]:
    """(heure, caractéristiques, P&L virtuel) pour chaque candidat observé avec un suivi COMPLET.

    Un suivi qui ne couvre pas toute la durée de détention possible (fenêtre pas encore écoulée, suivi de 15 min
    d'avant D-043, bot arrêté sans reprise) est écarté.
    """
    out = []
    for sh in ledger.shadows(kind=LAB_KIND):
        if rule is not None and not rule(sh["features"]):
            continue
        ticks = drop_glitches([t for t in ledger.shadow_ticks(sh["id"]) if t[1] is not None and t[1] > 0])
        if len(ticks) < 2:
            continue
        entry_ts, entry, liq = ticks[0]  # achat virtuel au premier prix relevé (~5 s après la décision)
        if entry is None:
            continue
        pos = {"token": sh["token"], "symbol": sh["symbol"], "opened_ts": entry_ts, "entry_price": entry,
               "entry_liquidity": liq, "qty": STAKE / entry, "cost_usd": STAKE, "entry_fee_usd": costs.fee(STAKE)}
        # Sans biais : on ne juge que les candidats dont les prix couvrent TOUTE la durée de détention possible.
        # Sinon, avec un suivi court, seuls les perdants (stop atteint tôt) auraient une sortie → biais.
        if ticks[-1][0] - entry_ts < cfg.max_hold_min * 60 - 120:
            continue
        res = replay_position(pos, ticks[1:], cfg, costs)
        if res is not None and res.complete:
            out.append((sh["ts"], sh["features"], res.pnl_usd))
    return out


def evaluate_lab(ledger: Ledger) -> tuple[int, list[HypothesisResult]]:
    shadows = ledger.shadows(kind=LAB_KIND)
    # Coupe au milieu de la période observée (ordre chronologique des candidats), la même pour toutes.
    cut = shadows[len(shadows) // 2]["ts"] if shadows else 0.0
    total = 0
    results = []
    for name, rule, cfg in HYPOTHESES:
        trades = lab_trades(ledger, cfg, rule=rule)
        total = max(total, len(trades))
        halves = (HalfStats(), HalfStats())
        for ts, _f, pnl in trades:
            h = halves[0] if ts < cut else halves[1]
            h.n += 1
            h.pnl += pnl
            h.wins += pnl > 0
        results.append(HypothesisResult(name, halves))
    return total, results


def render_lab(total: int, results: list[HypothesisResult]) -> str:
    lines = [f"Laboratoire : {total} candidats avec un suivi complet (achat virtuel 5 $ ~5 s après la décision, frais "
             "compris). Sortie 5 min : +8 % / −15 %. Sortie trend follower : stop −20 %, puis −25 % sous le sommet "
             "une fois +20 % atteint, 4 h max."]
    for r in results:
        a, b = r.halves
        lines.append(f"• {r.name} → {r.verdict}")
        for label, h in (("1re moitié", a), ("2e moitié", b)):
            rate = f"{h.wins / h.n:.0%}" if h.n else "—"
            lines.append(f"    {label} : {h.n} cas, total {h.pnl:+.2f} $, moyenne {h.avg:+.2f} $, gagnants {rate}")
    return "\n".join(lines)
