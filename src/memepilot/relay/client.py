"""Client « relais » : mêmes commandes et même format que gmgn-cli, servis par 3 API publiques gratuites.

- DexScreener  (prix, variations, achats/ventes 5 min, liquidité, pool) — sans clé, 300 req/min
- GeckoTerminal (tendances, nouveaux pools, bougies 1 min)            — sans clé, 30 req/min
- RugCheck      (sécurité : autorités, top 10, dev, insiders, risques) — sans clé (limite non publiée)

Lecture seule, HTTPS uniquement, trois hôtes autorisés, aucun secret. Chaque hôte a son propre rythme
minimal et son propre gel après un 429. Les flux smart money / KOL n'existent pas en relais : ces
commandes échouent proprement et le module smart money se met en retrait (confiance réduite).
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from memepilot.gmgn.read_client import ArgValue, GmgnCallFailed, GmgnRateLimited, GmgnReadClient, GmgnResult
from memepilot.gmgn.validation import validate_sol_address
from memepilot.relay import translate as tr

HOSTS: dict[str, tuple[str, float]] = {  # nom → (URL de base, intervalle minimal entre deux requêtes, s)
    "dexscreener": ("https://api.dexscreener.com", 0.25),
    # Annoncé 30 req/min, mesuré le 2026-11-01 : 429 dès ~5 requêtes rapprochées ; stable à 1 requête / 6–7 s.
    "gecko": ("https://api.geckoterminal.com/api/v2", 7.0),
    "rugcheck": ("https://api.rugcheck.xyz/v1", 0.6),
}
MAX_BYTES = 2_000_000
SECURITY_TTL_S = 240.0  # < TTL de fraîcheur « token_security » (300 s)
PAIR_TTL_S = 600.0
LIST_CACHE_S = 40.0  # < TTL de fraîcheur des listes (45 s) : la donnée garde sa VRAIE heure de réception
# Une seule liste GeckoTerminal par cycle (les deux sont demandées dans la même seconde) ; l'autre vient du cache
# ou attend le cycle suivant. Doit rester bien < à l'intervalle entre cycles (30 s, de début à début).
LIST_MIN_GAP_S = 12.0
KLINE_MIN_GAP_S = 25.0  # au plus 1 série de bougies par cycle via GeckoTerminal (quota serré)
KLINE_PAUSE_AFTER_429_S = 600.0  # après un 429 GeckoTerminal : 10 min sans bougies, les listes passent d'abord
RUG_BUDGET_PER_LIST = 10  # rapports RugCheck NOUVEAUX par liste et par cycle (le cache couvre le reste)

TRENDING_PATH = "/networks/solana/trending_pools?include=base_token&duration=1h"
NEW_POOLS_PATH = "/networks/solana/new_pools?include=base_token"

Fetcher = Callable[[str, float], Any]


def _http_get_json(url: str, timeout: float) -> Any:
    if not any(url.startswith(base + "/") for base, _ in HOSTS.values()):
        raise GmgnCallFailed("relais : hôte non autorisé")  # HTTPS + 3 hôtes fixes seulement
    req = urllib.request.Request(  # noqa: S310 — URL vérifiée ci-dessus
        url, headers={"Accept": "application/json", "User-Agent": "MemePilot/2 (papier)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — hôtes HTTPS fixes (HOSTS)
            body = r.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise _HttpStatus(exc.code) from exc
    if len(body) > MAX_BYTES:
        raise GmgnCallFailed("réponse trop volumineuse")
    return json.loads(body)


class _HttpStatus(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


class _Host:
    def __init__(self, base: str, min_interval: float) -> None:
        self.base = base
        self.min_interval = min_interval
        self.lock = asyncio.Lock()
        self.last = 0.0
        self.frozen_until = 0.0
        self.last_429 = -1e9

    @property
    def frozen_for(self) -> float:
        return max(0.0, self.frozen_until - time.monotonic())


class _RelayLimiter:
    """Expose `frozen_for` comme le régulateur GMGN (utilisé par le cycle et l'interface)."""

    def __init__(self, hosts: dict[str, _Host]) -> None:
        self._hosts = hosts

    @property
    def frozen_for(self) -> float:
        # Le relais est inutilisable seulement si DexScreener ET GeckoTerminal sont gelés.
        return min(self._hosts["dexscreener"].frozen_for, self._hosts["gecko"].frozen_for)


class RelayClient(GmgnReadClient):
    def __init__(self, fetch: Fetcher = _http_get_json, timeout_s: float = 12.0) -> None:
        super().__init__("gmgn-cli")  # jamais appelé : aucune commande ne part vers gmgn-cli
        self._fetch = fetch
        self._timeout = timeout_s
        self.hosts = {name: _Host(base, gap) for name, (base, gap) in HOSTS.items()}
        self.limiter = _RelayLimiter(self.hosts)  # type: ignore[assignment]
        self._sec_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._pair_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self.http_calls: dict[str, int] = {name: 0 for name in HOSTS}
        self._last_kline = 0.0
        self._last_list = 0.0
        self._list_cache: dict[str, tuple[float, Any]] = {}  # chemin → (heure de réception epoch, réponse)

    # ── HTTP ───────────────────────────────────────────────────────────────
    async def _get(self, host: str, path: str) -> Any:
        h = self.hosts[host]
        if h.frozen_for > 0:
            raise GmgnRateLimited(f"relais {host} : gel {h.frozen_for:.0f}s", h.frozen_for)
        async with h.lock:
            wait = h.min_interval - (time.monotonic() - h.last)
            if wait > 0:
                await asyncio.sleep(wait)
            h.last = time.monotonic()
        self.http_calls[host] += 1
        try:
            return await asyncio.to_thread(self._fetch, h.base + path, self._timeout)
        except _HttpStatus as exc:
            if exc.code == 429:
                h.frozen_until = time.monotonic() + 60
                h.last_429 = time.monotonic()
                raise GmgnRateLimited(f"relais {host} : 429 — gel 60s", 60) from exc
            raise GmgnCallFailed(f"relais {host} : HTTP {exc.code}") from exc
        except (OSError, ValueError, TimeoutError) as exc:
            raise GmgnCallFailed(f"relais {host} : {type(exc).__name__}") from exc

    def kline_ready(self) -> bool:
        """Une série de bougies peut-elle partir maintenant sans menacer le quota des listes ?"""
        g = self.hosts["gecko"]
        now = time.monotonic()
        return (g.frozen_for <= 0 and now - g.last_429 > KLINE_PAUSE_AFTER_429_S
                and now - self._last_kline >= KLINE_MIN_GAP_S)

    # ── briques ────────────────────────────────────────────────────────────
    async def pair(self, address: str) -> dict[str, Any] | None:
        hit = self._pair_cache.get(address)
        if hit and time.time() - hit[0] < 1:  # prix : cache très court (surveillance toutes les 2 s, D-030)
            return hit[1]
        data = await self._get("dexscreener", f"/tokens/v1/solana/{validate_sol_address(address)}")
        p = tr.best_pair(data, address)
        self._pair_cache[address] = (time.time(), p)
        return p

    async def batch_prices(self, addresses: list[str]) -> dict[str, tuple[float | None, float | None]]:
        """Prix et liquidité de plusieurs tokens en UNE requête DexScreener par lot de 30 (laboratoire D-041).

        Token absent ou sans pair fiable → (None, None). Un lot en erreur est simplement absent du résultat.
        """
        out: dict[str, tuple[float | None, float | None]] = {}
        for i in range(0, len(addresses), 30):
            chunk = [validate_sol_address(a) for a in addresses[i:i + 30]]
            try:
                data = await self._get("dexscreener", "/tokens/v1/solana/" + ",".join(chunk))
            except (GmgnCallFailed, GmgnRateLimited):
                continue
            for a in chunk:
                p = tr.best_pair(data, a)
                self._pair_cache[a] = (time.time(), p)
                if p is None:
                    out[a] = (None, None)
                else:
                    out[a] = (tr._f(p.get("priceUsd")), tr._f((p.get("liquidity") or {}).get("usd")))
        return out

    async def _pool_address(self, address: str) -> str | None:
        hit = self._pair_cache.get(address)
        p = hit[1] if hit and time.time() - hit[0] < PAIR_TTL_S else await self.pair(address)
        return p.get("pairAddress") if p else None

    async def security(self, address: str) -> dict[str, Any] | None:
        hit = self._sec_cache.get(address)
        if hit and time.time() - hit[0] < SECURITY_TTL_S:
            return hit[1]
        report = await self._get("rugcheck", f"/tokens/{validate_sol_address(address)}/report")
        if not isinstance(report, dict):
            return None
        self._sec_cache[address] = (time.time(), report)
        return report

    async def _gecko_list(self, path: str) -> tuple[Any, float]:
        """Une liste GeckoTerminal, en alternance (quota serré). Renvoie (réponse, heure RÉELLE de réception)."""
        hit = self._list_cache.get(path)
        # À qui le tour ? À la liste dont la copie est la plus ancienne (alternance tendances / nouveaux pools).
        turn = min((TRENDING_PATH, NEW_POOLS_PATH), key=lambda p: self._list_cache.get(p, (0.0, None))[0])
        if time.monotonic() - self._last_list < LIST_MIN_GAP_S or path != turn:
            if hit and time.time() - hit[0] < LIST_CACHE_S:
                return hit[1], hit[0]
            raise GmgnCallFailed("liste relais servie au cycle suivant (alternance, quota GeckoTerminal)")
        self._last_list = time.monotonic()
        data = await self._get("gecko", path)
        received = time.time()
        self._list_cache[path] = (received, data)
        return data, received

    async def _secured_rows(self, path: str, min_liq: float
                            ) -> tuple[list[tuple[dict[str, Any], dict[str, Any] | None]], float]:
        """Liste GeckoTerminal + sécurité RugCheck (cache d'abord, puis quelques nouveaux par cycle)."""
        payload, fetched_at = await self._gecko_list(path)
        rows = tr.gecko_pools(payload)
        rows = [r for r in rows if (r.get("liquidity") or 0) >= min_liq]  # filtre « serveur » comme GMGN
        rows.sort(key=lambda r: r.get("volume_h1") or 0, reverse=True)
        out: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        budget = RUG_BUDGET_PER_LIST
        for r in rows:
            hit = self._sec_cache.get(r["address"])
            report = hit[1] if hit and time.time() - hit[0] < SECURITY_TTL_S else None
            if report is None and budget > 0:
                budget -= 1
                try:
                    report = await self.security(r["address"])
                except GmgnRateLimited:
                    budget = 0
                except GmgnCallFailed:
                    report = None
            out.append((r, tr.rug_security(report) if report else None))  # sans sécurité → rejeté par le préfiltre
        return out, fetched_at

    # ── interface identique à GmgnReadClient ───────────────────────────────
    async def call(self, command: tuple[str, ...], cache_ttl_s: float | None = None,
                   **options: ArgValue) -> GmgnResult:
        start = time.perf_counter()
        self.calls += 1
        data, fetched_at = await self._dispatch(command, options)
        return GmgnResult(command, data, (time.perf_counter() - start) * 1000, "relais", fetched_at or time.time())

    async def _dispatch(self, command: tuple[str, ...], o: dict[str, ArgValue]) -> tuple[Any, float | None]:
        min_liq = float(o.get("min_liquidity", 0) or 0)  # type: ignore[arg-type]
        if command == ("token", "info"):
            addr = str(o["address"])
            return tr.token_info_response(addr, await self.pair(addr)), None
        if command == ("token", "pool"):
            addr = str(o["address"])
            return tr.pool_response(addr, await self.pair(addr)), None
        if command == ("token", "security"):
            addr = str(o["address"])
            rep = await self.security(addr)
            if rep is None:
                raise GmgnCallFailed("relais rugcheck : rapport vide")
            return tr.security_response(addr, rep), None
        if command == ("market", "trending") and str(o.get("interval", "1h")) != "1h":
            raise GmgnCallFailed("tendances 5 min : GMGN seulement (GeckoTerminal fournit les tendances 1 h)")
        if command == ("market", "trending"):
            rows, ts = await self._secured_rows(TRENDING_PATH, min_liq)
            return {"rank": [tr.trending_row(r, s) for r, s in rows]}, ts
        if command == ("market", "trenches"):
            rows, ts = await self._secured_rows(NEW_POOLS_PATH, min_liq)
            return tr.trenches_response(rows), ts
        if command == ("market", "kline"):
            if not self.kline_ready():
                raise GmgnCallFailed("bougies : 1 seule série par cycle en relais (quota GeckoTerminal)")
            self._last_kline = time.monotonic()
            addr = str(o["address"])
            pool = await self._pool_address(addr)
            if not pool:
                raise GmgnCallFailed("relais : pool introuvable pour les bougies")
            since = float(o.get("from", time.time() - 1200))  # type: ignore[arg-type]
            limit = max(5, min(100, int((time.time() - since) / 60) + 2))
            payload = await self._get("gecko", f"/networks/solana/pools/{validate_sol_address(pool)}/ohlcv/minute"
                                               f"?aggregate=1&limit={limit}&currency=usd")
            return tr.kline_response(payload, since), None
        raise GmgnCallFailed(f"relais : « {' '.join(command)} » indisponible (smart money / KOL = GMGN seulement)")
