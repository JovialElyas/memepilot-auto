"""Fraîcheur des données (SPEC F-04, G-04).

Toute donnée utilisée pour décider est enveloppée dans `Fresh` avec son heure de
réception et sa durée de validité. Lire une donnée expirée lève `StaleDataError` :
le scanner ne peut pas « décider » sur une donnée périmée, même par erreur.
(La démo GMGN réutilisait sans limite d'âge la dernière liste trending non vide.)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")

# Durées de validité par type de donnée (secondes). Valeurs de départ, ajustables en Phase 5.
TTL_S: dict[str, float] = {
    "sol_price": 30.0,
    "trending": 45.0,
    "trenches": 45.0,
    "kline": 60.0,
    "token_info": 30.0,
    "token_pool": 30.0,
    "token_security": 300.0,
    "holders": 120.0,
    "smart_money": 60.0,
    "creator": 3600.0,
}


class StaleDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class Fresh(Generic[T]):  # noqa: UP046 — compatibilité Python 3.11
    value: T
    fetched_at: float  # epoch (s)
    ttl_s: float
    kind: str = "data"

    @classmethod
    def of(cls, value: T, fetched_at: float, kind: str) -> Fresh[T]:
        if kind not in TTL_S:
            raise KeyError(f"type de donnée sans TTL : {kind}")
        return cls(value=value, fetched_at=fetched_at, ttl_s=TTL_S[kind], kind=kind)

    def age(self, now: float | None = None) -> float:
        return (time.time() if now is None else now) - self.fetched_at

    def is_fresh(self, now: float | None = None) -> bool:
        a = self.age(now)
        return -5.0 <= a <= self.ttl_s  # tolérance de 5 s pour un léger décalage d'horloge

    def get(self, now: float | None = None) -> T:
        if not self.is_fresh(now):
            raise StaleDataError(f"{self.kind} périmé ({self.age(now):.0f}s > {self.ttl_s:.0f}s)")
        return self.value
