"""Régulateur de débit global, pondéré par route (G-03, GMGN_CLI_AUDIT §3).

GMGN applique un « leaky bucket » par plan : Free 5/5, Plus 20/20, Pro 50/50
(débit en jetons/s / capacité). Chaque route coûte un poids. On reproduit ce seau
côté client, avec une marge de sécurité, pour ne jamais déclencher de 429 :
un bannissement se prolonge de 5 s par requête envoyée pendant le gel (max 5 min).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

# Poids officiels (skills gmgn-market / token / track / portfolio / swap, gmgn-cli 1.7.1).
ROUTE_WEIGHTS: dict[tuple[str, ...], int] = {
    ("token", "info"): 1,
    ("token", "security"): 1,
    ("token", "pool"): 1,
    ("token", "holders"): 5,
    ("token", "traders"): 5,
    ("market", "trending"): 3,
    ("market", "trenches"): 2,
    ("market", "signal"): 1,
    ("market", "kline"): 2,
    ("market", "hot-searches"): 3,
    ("portfolio", "info"): 2,
    ("portfolio", "holdings"): 2,
    ("portfolio", "activity"): 3,
    ("portfolio", "stats"): 3,
    ("portfolio", "profits"): 3,
    ("portfolio", "token-balance"): 2,
    ("portfolio", "created-tokens"): 2,
    ("track", "kol"): 1,
    ("track", "smartmoney"): 1,
    ("track", "follow-tokens"): 3,
    ("track", "follow-wallet"): 10,
    ("gas-price",): 1,
}

PLANS: dict[str, tuple[float, float]] = {"free": (5.0, 5.0), "plus": (20.0, 20.0), "pro": (50.0, 50.0)}

DEFAULT_BAN_S = 60.0
MAX_BAN_S = 300.0

_REMAINING = re.compile(r"~\s*(\d+)\s*s\s+remaining")
_RESET_AT = re.compile(r'"?reset_at"?\s*[:=]\s*(\d{9,})')


class PlanCapacityError(RuntimeError):
    """Le poids de la route dépasse la capacité du plan : la requête ne peut jamais réussir."""


@dataclass(frozen=True)
class RateLimitInfo:
    kind: str  # RATE_LIMIT_EXCEEDED | RATE_LIMIT_BANNED | RATE_LIMIT_BANNED_MAX | ERROR_RATE_LIMIT_BLOCKED | 429
    wait_s: float
    fatal: bool  # BANNED_MAX : arrêter TOUS les appels GMGN jusqu'au reset


def parse_rate_limit(stderr: str, now_epoch: float | None = None) -> RateLimitInfo | None:
    """Extrait le type de limite et le délai d'attente du message d'erreur de gmgn-cli."""
    if "429" not in stderr and "RATE_LIMIT" not in stderr:
        return None
    kind = "429"
    for k in ("RATE_LIMIT_BANNED_MAX", "ERROR_RATE_LIMIT_BLOCKED", "RATE_LIMIT_BANNED", "RATE_LIMIT_EXCEEDED"):
        if k in stderr:
            kind = k
            break
    wait: float | None = None
    m = _REMAINING.search(stderr)
    if m:
        wait = float(m.group(1)) + 1.0
    else:
        m2 = _RESET_AT.search(stderr)
        if m2:
            now = time.time() if now_epoch is None else now_epoch
            wait = max(0.0, float(m2.group(1)) - now) + 1.0
    if wait is None:
        wait = MAX_BAN_S if kind == "RATE_LIMIT_BANNED_MAX" else DEFAULT_BAN_S
    return RateLimitInfo(kind=kind, wait_s=min(wait, MAX_BAN_S + 5), fatal=kind == "RATE_LIMIT_BANNED_MAX")


class RateLimiter:
    """Seau à jetons pondéré, partagé par tous les appels GMGN du processus."""

    def __init__(
        self,
        plan: str = "free",
        safety: float = 0.8,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if plan not in PLANS:
            raise ValueError(f"plan inconnu : {plan}")
        if not 0 < safety <= 1:
            raise ValueError("safety doit être dans ]0, 1]")
        rate, capacity = PLANS[plan]
        self.plan = plan
        self.rate = rate * safety
        self.capacity = capacity
        # D-036 : rafale plafonnée elle aussi (5 → 4 en Free). Les sous-processus gmgn-cli démarrent avec un
        # délai variable : des requêtes autorisées à des instants différents peuvent ARRIVER groupées chez GMGN.
        self.burst = capacity * safety
        self._clock = clock
        self._sleep = sleep
        self._tokens = self.burst
        self._last = clock()
        self._frozen_until = 0.0
        self._lock = asyncio.Lock()

    def weight_of(self, command: tuple[str, ...]) -> int:
        return ROUTE_WEIGHTS.get(command, 5)  # route inconnue : poids prudent

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
        self._last = now

    @property
    def frozen_for(self) -> float:
        return max(0.0, self._frozen_until - self._clock())

    async def acquire(self, command: tuple[str, ...]) -> int:
        weight = self.weight_of(command)
        if weight > self.capacity:
            raise PlanCapacityError(
                f"{' '.join(command)} (poids {weight}) dépasse la capacité du plan {self.plan} "
                f"({self.capacity:g}) : PLAN_UPGRADE_REQUIRED"
            )
        async with self._lock:
            while True:
                freeze = self.frozen_for
                if freeze > 0:
                    await self._sleep(freeze)
                    continue
                self._refill()
                if self._tokens >= min(weight, self.burst):
                    self._tokens -= weight
                    return weight
                await self._sleep((weight - self._tokens) / self.rate)

    def freeze(self, seconds: float) -> None:
        """Gel global après un 429 : plus aucune requête avant l'échéance, seau vidé."""
        until = self._clock() + max(0.0, seconds)
        self._frozen_until = max(self._frozen_until, until)
        self._tokens = 0.0
        self._last = self._clock()
