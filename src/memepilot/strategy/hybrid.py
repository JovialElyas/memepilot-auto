"""Hybrid Strategy Agent (STRATEGY §4) — déterministe, aucune exécution.

score = [Σ(poids × score × confiance) / Σ(poids × confiance)] × couverture,
  couverture = Σ(poids × confiance) / Σ(poids) sur les modules qui ne s'abstiennent pas.
PROPOSITION seulement si :
  - aucun veto (tous modules confondus) ;
  - ≥ 2 modules confirment (score ≥ 60 et confiance ≥ 0,5) ;
  - score agrégé ≥ 70 ;
  - classe de risque ≠ HIGH (V2 : préservation du capital → on ne trade pas le risque élevé).
Sinon : NO_TRADE, avec la raison. NO_TRADE est le résultat par défaut.

Une PROPOSITION n'est pas un ordre : la taille finale et l'autorisation appartiennent
au Risk Guardian (Phase 4), l'envoi à l'Execution Engine (Phase 6).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from memepilot.scanner.prefilter import Candidate
from memepilot.signals.base import SignalReport


class Decision(StrEnum):
    PROPOSE = "PROPOSE"
    NO_TRADE = "NO_TRADE"


class RiskClass(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class HybridConfig:
    weights: dict[str, float] = field(
        default_factory=lambda: {"momentum": 0.35, "smart_money": 0.45, "early_token": 0.20}
    )
    propose_threshold: float = 70.0
    confirm_score: float = 60.0
    confirm_confidence: float = 0.5
    min_confirming: int = 2
    # Plan de sortie proposé (STRATEGY §5) — appliqué par l'Execution Engine en Phase 6
    stop_loss_pct: float = 15.0  # D-040
    take_profit_pct: float = 8.0  # D-040 : vente totale
    take_profit_sell_ratio: float = 50.0
    trailing_drawdown_pct: float = 10.0  # D-026 : armé dès +15 % (paper/exits.py)
    medium_size: float = 1.0  # D-033 : taille pleine aussi en risque MEDIUM (avant : demi-taille 0,5)
    time_stop_min: int = 5  # D-040 : durée max
    profile: str = "strict"
    # Strict : score escompté par la couverture des données. Exploration (D-025) : pas d'escompte, mais une
    # couverture minimale exigée — sinon, sans bougies ni flux smart money (relais), le maximum atteignable
    # était 52 < seuil 55 : aucun trade n'était possible, même sur un token parfait.
    coverage_discount: bool = True
    min_coverage: float = 0.0


STRICT = HybridConfig()
# B-10 / D-021 : profil d'EXPLORATION, réservé au papier, pour produire des données de test.
# Il assouplit seulement la stratégie (seuil 55, une seule confirmation). Il ne touche NI aux
# filtres de sécurité, NI aux vetos, NI au Risk Guardian, et refuse toujours le risque HIGH.
# Mesure réelle (2026-11-01) : le flux smart money (200 trades pour tout Solana) ne voit presque jamais un
# token donné ; avec un poids de 45 %, ce « non vu » plafonnait à ~49 des tokens à momentum 84 / early 98.
# L'exploration repondère donc vers les données observées token par token. Le profil strict est inchangé.
EXPLORATION = HybridConfig(
    weights={"momentum": 0.45, "smart_money": 0.25, "early_token": 0.30},
    propose_threshold=55.0, confirm_score=55.0, min_confirming=1, profile="exploration",
    coverage_discount=False, min_coverage=0.5,
)
PROFILES = {"strict": STRICT, "exploration": EXPLORATION}


class ProfileNotAllowed(ValueError):
    pass


def profile_config(name: str, mode: str) -> HybridConfig:
    """Le profil d'exploration est INTERDIT hors papier (vérifié par le code et par les tests)."""
    if name not in PROFILES:
        raise ProfileNotAllowed(f"profil inconnu : {name}")
    if name != "strict" and mode != "paper":
        raise ProfileNotAllowed(f"le profil {name} est réservé au mode papier")
    return PROFILES[name]


@dataclass(frozen=True)
class HybridDecision:
    address: str
    symbol: str | None
    decision: Decision
    score: float
    risk_class: RiskClass
    size_fraction: float  # 1.0 / 0.5 / 0 — indicatif, le Risk Guardian décide
    reasons: tuple[str, ...]
    signals: tuple[SignalReport, ...]
    exit_plan: dict[str, float] | None = None
    profile: str = "strict"

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address, "symbol": self.symbol, "decision": str(self.decision), "profile": self.profile,
            "score": self.score, "risk_class": str(self.risk_class), "size_fraction": self.size_fraction,
            "reasons": list(self.reasons), "exit_plan": self.exit_plan,
            "signals": {s.module: {"score": s.score, "confidence": s.confidence, "reasons": list(s.reasons),
                                   "vetoes": list(s.vetoes)} for s in self.signals},
        }


def risk_class(c: Candidate) -> RiskClass:
    rug = c.rug_ratio if c.rug_ratio is not None else 1.0
    liq = c.liquidity or 0.0
    if rug > 0.20 or liq < 20_000 or (c.age_min is not None and c.age_min < 30) or c.warnings and any(
        "désaccord" in w or "incohérente" in w for w in c.warnings
    ):
        return RiskClass.HIGH
    if rug > 0.10 or liq < 50_000 or c.bundler is None or c.rat_trader is None:
        return RiskClass.MEDIUM
    return RiskClass.LOW


def decide(c: Candidate, signals: Sequence[SignalReport], cfg: HybridConfig = HybridConfig()) -> HybridDecision:  # noqa: B008
    rc = risk_class(c)

    def no(reason: str, score: float = 0.0) -> HybridDecision:
        return HybridDecision(c.address, c.symbol, Decision.NO_TRADE, round(score, 1), rc, 0.0, (reason,),
                              tuple(signals), profile=cfg.profile)

    vetoes = [f"{s.module}: {v}" for s in signals for v in s.vetoes]
    active = [s for s in signals if not s.abstains]
    if not active:
        return no("aucun module n'a assez de données")
    num = sum(cfg.weights.get(s.module, 0) * s.score * s.confidence for s in active)
    den = sum(cfg.weights.get(s.module, 0) * s.confidence for s in active)
    wsum = sum(cfg.weights.get(s.module, 0) for s in active)
    # Le score est escompté par la couverture des données : 88/100 obtenu avec des confiances
    # de 0,2 et 0,4 ne vaut pas 88 (cas réel constaté le 2026-10-31 : MOGZ → ~27).
    coverage = den / wsum if wsum else 0.0
    score = (num / den) * (coverage if cfg.coverage_discount else 1.0) if den else 0.0
    if vetoes:
        return no("veto — " + " ; ".join(vetoes), score)
    if coverage < cfg.min_coverage:
        return no(f"données trop incomplètes (couverture {coverage:.0%} < {cfg.min_coverage:.0%})", score)
    confirming = [s for s in active if s.score >= cfg.confirm_score and s.confidence >= cfg.confirm_confidence]
    if len(confirming) < cfg.min_confirming:
        names = ", ".join(s.module for s in confirming) or "aucun"
        return no(f"confirmation croisée insuffisante ({names})", score)
    if score < cfg.propose_threshold:
        return no(f"score {score:.0f} < {cfg.propose_threshold:.0f}", score)
    if rc is RiskClass.HIGH:
        return no("classe de risque HIGH", score)
    size = 1.0 if rc is RiskClass.LOW else cfg.medium_size
    plan = {"stop_loss_pct": cfg.stop_loss_pct, "take_profit_pct": cfg.take_profit_pct,
            "take_profit_sell_ratio": cfg.take_profit_sell_ratio,
            "trailing_drawdown_pct": cfg.trailing_drawdown_pct, "time_stop_min": float(cfg.time_stop_min)}
    why = f"score {score:.0f}, confirmé par {', '.join(s.module for s in confirming)}, risque {rc}"
    if cfg.profile != "strict":
        why += f" [profil {cfg.profile}]"
    return HybridDecision(c.address, c.symbol, Decision.PROPOSE, round(score, 1), rc, size, (why,),
                          tuple(signals), plan, profile=cfg.profile)
