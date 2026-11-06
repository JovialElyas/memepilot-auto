"""Contrat commun des modules de signaux (STRATEGY §3).

Chaque module renvoie un `SignalReport` :
- score 0–100 (qualité du signal) ;
- confiance 0–1 (quantité/qualité des données disponibles ; 0 = le module s'abstient) ;
- raisons lisibles (chaque point gagné ou perdu est expliqué) ;
- vetos : conditions qui interdisent un trade, quel que soit le score.

Règle : une donnée absente baisse la confiance, elle n'augmente jamais le score.
Les modules sont des fonctions pures : aucun appel réseau, aucun LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


@dataclass(frozen=True)
class SignalReport:
    module: str
    score: float  # 0–100
    confidence: float  # 0–1
    reasons: tuple[str, ...] = ()
    vetoes: tuple[str, ...] = ()

    @property
    def abstains(self) -> bool:
        return self.confidence <= 0.0


@dataclass
class _Builder:
    module: str
    parts: list[tuple[float, float]] = field(default_factory=list)  # (sous-score 0–1, poids)
    missing: int = 0
    total: int = 0
    reasons: list[str] = field(default_factory=list)
    vetoes: list[str] = field(default_factory=list)

    def add(self, name: str, value: float | None, weight: float, why: str | None = "") -> None:
        """Ajoute un sous-score (0–1). `None` = donnée absente : pas de points, confiance réduite."""
        self.total += 1
        if value is None:
            self.missing += 1
            self.reasons.append(f"{name}: absent")
            return
        v = clamp(value)
        self.parts.append((v, weight))
        self.reasons.append(f"{name}: {v * 100:.0f}/100" + (f" ({why})" if why else ""))

    def veto(self, reason: str) -> None:
        self.vetoes.append(reason)

    def build(self, base_confidence: float = 1.0) -> SignalReport:
        if not self.parts:
            return SignalReport(self.module, 0.0, 0.0, tuple(self.reasons), tuple(self.vetoes))
        wsum = sum(w for _, w in self.parts)
        score = 100.0 * sum(v * w for v, w in self.parts) / wsum
        coverage = (self.total - self.missing) / self.total if self.total else 0.0
        conf = clamp(base_confidence * coverage)
        return SignalReport(self.module, round(score, 1), round(conf, 2), tuple(self.reasons), tuple(self.vetoes))
