"""Module Momentum (STRATEGY §3.1, idées R10/R11 de la démo).

Deux niveaux de données :
1. ligne trending : variations 5 min / 1 h et rapport achats/ventes (gratuit, déjà chargé) ;
2. bougies 1 min (`market kline`, poids 2) pour les meilleurs candidats seulement :
   accélération, volatilité, anti-FOMO, mèche de rejet.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from memepilot.gmgn.models import Candle
from memepilot.scanner.prefilter import Candidate
from memepilot.signals.base import SignalReport, _Builder


@dataclass(frozen=True)
class MomentumConfig:
    buy_ratio_veto: float = 0.42  # pression vendeuse dominante (démo R11)
    bleed_1h: float = -0.12  # 1 h ≤ −12 % …
    bleed_5m: float = -0.06  # … et 5 min ≤ −6 % → baisse continue
    fomo_5m: float = 0.50  # +50 % en 5 min = pompe verticale → on n'achète pas le sommet
    # D-026 : « achat de sommet » détectable SANS bougies (relais) : déjà +100 % sur 1 h ET encore +20 % sur 5 min
    chase_1h: float = 1.00  # D-029 : déjà +100 % sur 1 h → refus, même si les 5 dernières minutes sont calmes
    chase_5m: float = 0.20
    # D-029 (10 premiers trades réels) : les achats perdants étaient faits EN PLEIN PIC ; ceux qui sont montés,
    # pendant que le prix se reposait. On n'achète plus une hausse en cours, on attend qu'elle se calme.
    spike_5m: float = 0.12  # +12 % ou plus sur 5 min (sans bougies) → pic en cours
    spike_accel: float = 0.10  # bougies : accélération > +10 % sur les dernières minutes → pic en cours
    max_volatility_1m: float = 0.15  # écart-type des rendements 1 min au-delà duquel on pénalise
    wick_ratio: float = 2.0  # mèche haute > 2× le corps sur la dernière bougie = rejet


def candle_features(candles: Sequence[Candle]) -> dict[str, float] | None:
    """Mesures sur les 15 dernières bougies 1 min (None si moins de 10 bougies)."""
    c = list(candles)[-15:]
    if len(c) < 10:
        return None
    closes = [x.close for x in c]
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    recent = closes[-1] / closes[-6] - 1  # 5 dernières minutes
    prior = closes[-6] / closes[0] - 1  # minutes précédentes
    mean = sum(rets) / len(rets)
    vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets))
    last = c[-1]
    body = abs(last.close - last.open) or last.close * 1e-6
    upper_wick = last.high - max(last.open, last.close)
    return {"ret_5m": recent, "ret_prior": prior, "accel": recent - prior / 2, "vol": vol,
            "wick": upper_wick / body}


def momentum_signal(c: Candidate, candles: Sequence[Candle] | None = None,
                    cfg: MomentumConfig = MomentumConfig()) -> SignalReport:  # noqa: B008
    b = _Builder("momentum")

    # Vetos (données de la ligne)
    if c.buy_ratio is not None and c.buy_ratio < cfg.buy_ratio_veto:
        b.veto(f"pression vendeuse (achats {c.buy_ratio:.0%})")
    if c.chg_1h is not None and c.chg_5m is not None and c.chg_1h <= cfg.bleed_1h and c.chg_5m <= cfg.bleed_5m:
        b.veto("baisse continue 1 h et 5 min")
    if c.chg_1h is not None and c.chg_1h >= cfg.chase_1h:
        b.veto(f"achat de sommet (déjà {c.chg_1h:+.0%} sur 1 h)")
    if c.chg_5m is not None and c.chg_5m >= cfg.spike_5m:
        b.veto(f"pic en cours ({c.chg_5m:+.0%} sur 5 min) : attendre que le prix se calme")

    b.add("variation 5 min", None if c.chg_5m is None else (c.chg_5m + 0.05) / 0.30, 3,
          None if c.chg_5m is None else f"{c.chg_5m:+.0%}")
    b.add("variation 1 h", None if c.chg_1h is None else (c.chg_1h + 0.10) / 0.60, 1.5,
          None if c.chg_1h is None else f"{c.chg_1h:+.0%}")
    b.add("pression acheteuse", None if c.buy_ratio is None else (c.buy_ratio - 0.45) / 0.25, 2,
          None if c.buy_ratio is None else f"{c.buy_ratio:.0%} d'achats")

    feats = candle_features(candles) if candles else None
    base_conf = 0.6  # sans bougies, le momentum reste une estimation grossière
    if feats is not None:
        base_conf = 1.0
        b.add("accélération (bougies)", 0.5 + feats["accel"] / 0.20, 2.5, f"{feats['accel']:+.1%}")
        b.add("volatilité maîtrisée", 1 - feats["vol"] / cfg.max_volatility_1m, 1, f"σ={feats['vol']:.1%}")
        if feats["accel"] > cfg.spike_accel:
            b.veto(f"pic en cours (accélération {feats['accel']:+.0%} sur les bougies) : attendre que le prix se calme")
        if feats["ret_5m"] >= cfg.fomo_5m:
            b.veto(f"pompe verticale (+{feats['ret_5m']:.0%} en 5 min)")
        if feats["wick"] >= cfg.wick_ratio and feats["ret_5m"] > 0:
            b.veto("mèche de rejet sur la dernière bougie")
    elif candles is not None:
        b.reasons.append("bougies insuffisantes (< 10)")

    rep = b.build(base_confidence=base_conf)
    # Une pénalité douce : la course déjà très avancée sur 1 h réduit le score (risque de sommet).
    if c.chg_1h is not None and c.chg_1h > 3.0:
        rep = SignalReport(rep.module, round(rep.score * 0.8, 1), rep.confidence,
                           (*rep.reasons, f"déjà +{c.chg_1h:.0%} sur 1 h : −20 %"), rep.vetoes)
    return rep


__all__ = ["MomentumConfig", "candle_features", "momentum_signal"]
