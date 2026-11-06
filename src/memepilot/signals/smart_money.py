"""Module Smart Money (STRATEGY §3.2).

Sources :
- comptes dans la ligne (smart_degen_count, renowned_count) : « combien sont dedans » ;
- flux récent `track smartmoney` + `track kol` (1 appel chacun, poids 1) : « qui achète / vend
  MAINTENANT », en wallets DISTINCTS, avec décroissance dans le temps.

Les sorties de la smart money sont un signal négatif fort (veto si elles dominent).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from memepilot.gmgn.models import TrackedTrade
from memepilot.scanner.prefilter import Candidate
from memepilot.signals.base import SignalReport, _Builder


@dataclass(frozen=True)
class SmartMoneyConfig:
    window_s: float = 1800.0  # 30 min
    half_life_s: float = 600.0  # un achat perd la moitié de son poids en 10 min
    min_buy_usd: float = 50.0  # ignorer la poussière
    exit_veto_ratio: float = 1.0  # vendeurs distincts ≥ acheteurs distincts → veto


@dataclass
class TokenFlow:
    buyers: set[str] = field(default_factory=set)
    sellers: set[str] = field(default_factory=set)
    weighted_buyers: float = 0.0
    buy_usd: float = 0.0
    sell_usd: float = 0.0
    kol_buyers: set[str] = field(default_factory=set)
    last_buy_ts: int = 0


def build_flows(trades: Iterable[TrackedTrade], now: float, cfg: SmartMoneyConfig = SmartMoneyConfig(),  # noqa: B008
                kol_makers: set[str] | None = None) -> dict[str, TokenFlow]:
    """Agrège le flux par token, en ne comptant chaque wallet qu'une fois par sens."""
    flows: dict[str, TokenFlow] = defaultdict(TokenFlow)
    seen_weight: dict[tuple[str, str], float] = {}
    for t in trades:
        age = now - t.timestamp
        if age < -60 or age > cfg.window_s:
            continue
        f = flows[t.token]
        usd = t.amount_usd or 0.0
        if t.side == "buy":
            if usd < cfg.min_buy_usd:
                continue
            f.buyers.add(t.maker)
            f.buy_usd += usd
            f.last_buy_ts = max(f.last_buy_ts, t.timestamp)
            w = math.pow(0.5, max(0.0, age) / cfg.half_life_s)
            key = (t.token, t.maker)
            seen_weight[key] = max(seen_weight.get(key, 0.0), w)  # un wallet = un poids max
            if kol_makers and t.maker in kol_makers:
                f.kol_buyers.add(t.maker)
        else:
            f.sellers.add(t.maker)
            f.sell_usd += usd
    for (token, _maker), w in seen_weight.items():
        flows[token].weighted_buyers += w
    return dict(flows)


def smart_money_signal(c: Candidate, flow: TokenFlow | None, feed_available: bool,
                       cfg: SmartMoneyConfig = SmartMoneyConfig()) -> SignalReport:  # noqa: B008
    # D-038 : le smart money est un BONUS. Son absence (aucun wallet suivi détenteur, aucun achat récent, flux
    # coupé) ne prouve rien : le module s'abstient au lieu de noter 0, ce qui faisait baisser la note des autres
    # modules (cas Leverhedge : 54 au lieu de ~72). La revente par la smart money reste un veto.
    b = _Builder("smart_money")
    n_hold = (c.smart_degen or 0) + (c.renowned or 0)
    if n_hold > 0:
        b.add("smart money détentrice", math.log10(1 + n_hold) / math.log10(1 + 20), 1.5,
              f"{c.smart_degen or 0} SM + {c.renowned or 0} KOL")  # 20 wallets suivis → plein score

    if not feed_available:
        b.reasons.append("flux temps réel indisponible")
        return b.build(base_confidence=0.4)

    if flow is None or (not flow.buyers and not flow.sellers):
        b.reasons.append("aucun achat smart money récent (neutre)")
        return b.build(base_confidence=0.8)

    if not flow.buyers:
        b.veto(f"sortie smart money ({len(flow.sellers)} vendeurs, aucun acheteur)")
        return b.build(base_confidence=1.0)
    b.add("achats récents (30 min)", flow.weighted_buyers / 3.0, 3,
          f"{len(flow.buyers)} wallets distincts, {flow.buy_usd:,.0f} $")
    if flow.kol_buyers:
        b.add("KOL acheteurs", len(flow.kol_buyers) / 2, 1, f"{len(flow.kol_buyers)} KOL")
    if flow.sellers:
        ratio = len(flow.sellers) / max(1, len(flow.buyers))
        b.reasons.append(f"vendeurs : {len(flow.sellers)} wallets, {flow.sell_usd:,.0f} $")
        if ratio >= cfg.exit_veto_ratio:
            b.veto(f"sortie smart money ({len(flow.sellers)} vendeurs ≥ {len(flow.buyers)} acheteurs)")
    return b.build(base_confidence=1.0)
