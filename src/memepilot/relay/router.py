"""Aiguillage GMGN ↔ relais (D-024).

Modes :
- "gmgn"  : GMGN seulement (comportement d'avant) ;
- "relay" : relais seulement (DexScreener + GeckoTerminal + RugCheck) ;
- "auto"  : GMGN d'abord ; si GMGN est gelé ou répond « limite de débit » / délai dépassé, la MÊME commande
            est servie par le relais. GMGN redevient prioritaire dès la fin de son gel.

Le reste du bot ne voit qu'un client au format GMGN. `source_counts` dit qui a servi quoi (interface, ledger).
"""

from __future__ import annotations

from typing import Any

from memepilot.gmgn.read_client import ArgValue, GmgnCallFailed, GmgnError, GmgnRateLimited, GmgnReadClient, GmgnResult
from memepilot.relay.client import RelayClient

MODES = ("auto", "gmgn", "relay")
_RELAYABLE = frozenset({("token", "info"), ("token", "pool"), ("token", "security"), ("market", "trending"),
                        ("market", "trenches"), ("market", "kline")})
# Bougies relayées avec parcimonie (D-025) : 1 série par cycle au plus, et aucune pendant 10 min après un 429
# GeckoTerminal — le quota (~10 req/min mesuré) sert d'abord aux listes.


class _RouterLimiter:
    def __init__(self, router: RoutedClient) -> None:
        self._r = router

    @property
    def frozen_for(self) -> float:
        g, rl = self._r.primary.limiter.frozen_for, self._r.relay.limiter.frozen_for
        if self._r.mode == "gmgn":
            return g
        if self._r.mode == "relay":
            return rl
        return min(g, rl)  # en auto, on n'est bloqué que si les DEUX sources le sont


class RoutedClient(GmgnReadClient):
    def __init__(self, primary: GmgnReadClient, relay: RelayClient | None = None, mode: str = "auto") -> None:
        if mode not in MODES:
            raise ValueError(f"mode de source inconnu : {mode}")
        super().__init__("gmgn-cli")
        self.primary = primary
        self.relay = relay or RelayClient()
        self.mode = mode
        self.limiter = _RouterLimiter(self)  # type: ignore[assignment]
        self.source_counts: dict[str, int] = {"gmgn": 0, "relais": 0}
        self.last_fallback_reason = ""

    def can_serve(self, command: tuple[str, ...]) -> bool:
        if command == ("market", "kline") and self.mode != "gmgn" and (
                self.mode == "relay" or self.primary.limiter.frozen_for > 0):
            return self.relay.kline_ready()
        if self.mode == "relay":
            return command in _RELAYABLE and self.relay.limiter.frozen_for <= 0
        if self.mode == "auto" and command in _RELAYABLE and self.relay.limiter.frozen_for <= 0:
            return True
        return self.primary.limiter.frozen_for <= 0

    async def quote(self, address: str) -> GmgnResult:
        """Prix d'une position (D-036) : relais DexScreener d'abord (300 req/min), GMGN en secours.

        La surveillance toutes les 2 s consommait ~1 req/s du quota GMGN Free (4/s) et provoquait des gels
        qui bloquaient le flux smart money. Le quota GMGN est désormais réservé au scan et au smart money.
        """
        if self.mode != "gmgn" and self.relay.limiter.frozen_for <= 0:
            try:
                res = await self.relay.call(("token", "info"), None, chain="sol", address=address)
                self.calls += 1
                self.source_counts["relais"] += 1
                return res
            except GmgnError:
                if self.mode == "relay":
                    raise
        return await self.call(("token", "info"), chain="sol", address=address)

    async def batch_prices(self, addresses: list[str]) -> dict[str, tuple[float | None, float | None]]:
        """Prix groupés (laboratoire D-041) : toujours par le relais, le quota GMGN n'est pas touché."""
        return await self.relay.batch_prices(addresses)

    def kline_budget(self, wanted: int) -> int:
        relayed = self.mode == "relay" or (self.mode == "auto" and self.primary.limiter.frozen_for > 0)
        return min(wanted, 1) if relayed else wanted

    async def _use_relay(self, command: tuple[str, ...], cache_ttl_s: float | None,
                         options: dict[str, ArgValue]) -> GmgnResult:
        res = await self.relay.call(command, cache_ttl_s, **options)
        self.calls += 1
        self.source_counts["relais"] += 1
        return res

    async def call(self, command: tuple[str, ...], cache_ttl_s: float | None = None,
                   **options: ArgValue) -> GmgnResult:
        relayable = command in _RELAYABLE and not (
            command == ("market", "trending") and options.get("interval", "1h") != "1h")  # pas de 5 min en relais
        if self.mode == "relay" or (self.mode == "auto" and relayable and self.primary.limiter.frozen_for > 0):
            if self.mode == "auto":
                self.last_fallback_reason = "GMGN gelé"
            return await self._use_relay(command, cache_ttl_s, options)
        try:
            res = await self.primary.call(command, cache_ttl_s, **options)
            self.calls += 1
            self.source_counts["gmgn"] += 1
            return res
        except (GmgnRateLimited, GmgnCallFailed) as exc:
            if self.mode != "auto" or not relayable or (isinstance(exc, GmgnCallFailed) and not _transient(exc)):
                raise
            self.last_fallback_reason = str(exc)[:80]
            return await self._use_relay(command, cache_ttl_s, options)

    def describe(self) -> dict[str, Any]:
        return {"mode": self.mode, **self.source_counts, "relay_http": dict(self.relay.http_calls),
                "fallback_reason": self.last_fallback_reason}


def _transient(exc: GmgnError) -> bool:
    """Erreurs GMGN passagères qui justifient le relais (délai, réseau, 5xx) — pas une réponse invalide."""
    s = str(exc).lower()
    return any(k in s for k in ("délai dépassé", "timeout", "econn", "network", "503", "502", "500", "429"))
