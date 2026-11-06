"""Relais gratuit (D-024) : traduction au format GMGN, prudence sur les absences, aiguillage GMGN → relais."""

import json
import time
from pathlib import Path
from typing import Any

import pytest

from memepilot.gmgn.models import TokenInfo, TokenPool, TokenSecurity, parse_kline, parse_trenches, parse_trending
from memepilot.gmgn.read_client import ArgValue, GmgnCallFailed, GmgnRateLimited, GmgnReadClient, GmgnResult
from memepilot.relay import translate as tr
from memepilot.relay.client import HOSTS, NEW_POOLS_PATH, TRENDING_PATH, RelayClient, _http_get_json
from memepilot.relay.router import RoutedClient
from memepilot.scanner.prefilter import Candidate, prefilter

FX = Path(__file__).resolve().parents[1] / "fixtures" / "relay"
TOKEN = "CPoGSmcjprCvzA2MFYLJMpQkGjmgVzKg4RVwMdrzpump"
POOL = "BzomHwB33RGwuyWdm8sQFi3ZDNaUWnpmHRaiQaLQNZiA"


def load(name: str) -> Any:
    return json.loads((FX / name).read_text(encoding="utf-8"))


# ── traduction pure ────────────────────────────────────────────────────────
def test_rugcheck_security_excludes_pool_from_top10() -> None:
    s = tr.rug_security(load("rugcheck_report.json"))
    assert s["renounced_mint"] is True and s["renounced_freeze_account"] is True
    assert s["rug_ratio"] == 0.0 and s["is_honeypot"] is None
    # le 1er détenteur (7,56 %) est le pool lui-même : exclu → top 10 ≈ 20 % et non ≈ 26 %
    assert s["top_10_holder_rate"] is not None and 0.15 < s["top_10_holder_rate"] < 0.22
    assert s["dev_team_hold_rate"] == 0.0 and s["holder_count"] == 9049
    assert s["suspected_insider_hold_rate"] is not None and s["suspected_insider_hold_rate"] > 0


def test_rugcheck_danger_or_rugged_rejects_and_absent_stays_absent() -> None:
    rep = load("rugcheck_report.json")
    assert tr.rug_security({**rep, "risks": [{"name": "Freeze", "level": "danger"}]})["rug_ratio"] == 1.0
    assert tr.rug_security({**rep, "rugged": True})["rug_ratio"] == 1.0
    empty = tr.rug_security({})
    assert all(empty[k] is None for k in ("renounced_mint", "renounced_freeze_account", "top_10_holder_rate",
                                          "dev_team_hold_rate", "rug_ratio"))


def test_gecko_trending_translates_and_passes_strict_prefilter() -> None:
    rows = tr.gecko_pools(load("gecko_trending.json"))
    assert rows and all(r["address"] not in tr.QUOTE_MINTS for r in rows)
    blue = next(r for r in rows if r["address"] == TOKEN)
    assert blue["symbol"] == "BLUE" and blue["liquidity"] and blue["wash"] is False
    sec = tr.rug_security(load("rugcheck_report.json"))
    parsed = parse_trending({"rank": [tr.trending_row(blue, sec)]})
    assert not parsed.errors
    c = Candidate.from_trending(parsed.items[0])
    assert prefilter(c).passed, prefilter(c).reasons
    # Sans rapport de sécurité : rejet (jamais « sûr » par défaut)
    c2 = Candidate.from_trending(parse_trending({"rank": [tr.trending_row(blue, None)]}).items[0])
    v = prefilter(c2)
    assert not v.passed and "mint:absent" in v.reasons and "rug_ratio:absent" in v.reasons


def test_wash_heuristic() -> None:
    assert tr._wash({"buys": 400, "sells": 400, "buyers": 40, "sellers": 40}) is True
    assert tr._wash({"buys": 400, "sells": 400, "buyers": 300, "sellers": 200}) is False
    assert tr._wash({"buys": 10, "sells": 5}) is False
    assert tr._wash({}) is None


def test_gecko_new_pools_to_trenches() -> None:
    rows = tr.gecko_pools(load("gecko_new.json"))
    resp = tr.trenches_response([(r, None) for r in rows])
    parsed = parse_trenches(resp)
    assert not parsed.errors and len(parsed.items) == len(rows)
    assert all(t.category in ("new_creation", "completed") for t in parsed.items)


def test_dexscreener_token_info_and_pool() -> None:
    pair = tr.best_pair(load("dexscreener_tokens.json"), TOKEN)
    assert pair is not None
    info = TokenInfo.from_api(tr.token_info_response(TOKEN, pair))
    assert info.symbol == "BLUE" and not info.is_unknown and info.price_usd
    assert info.price_5m and info.price_1h and info.buys_5m is not None
    # variation 5 min reconstruite = celle publiée par DexScreener
    assert info.price_usd / info.price_5m - 1 == pytest.approx(pair["priceChange"]["m5"] / 100, rel=1e-6)
    pool = TokenPool.model_validate(tr.pool_response(TOKEN, pair))
    assert pool.liquidity and pool.pool_address
    assert TokenInfo.from_api(tr.token_info_response(TOKEN, None)).is_unknown


def test_sol_price_from_dexscreener_passes_validation() -> None:
    from memepilot.market.prices import validate_sol_price
    pair = tr.best_pair(load("dexscreener_tokens.json"), tr.SOL_MINT)
    assert 10 < validate_sol_price(TokenInfo.from_api(tr.token_info_response(tr.SOL_MINT, pair))) < 1000


def test_security_response_and_kline() -> None:
    sec = TokenSecurity.from_api(tr.security_response(TOKEN, load("rugcheck_report.json")))
    assert sec.renounced_mint is True and sec.is_honeypot is None
    ohlcv = load("gecko_ohlcv.json")
    first = ohlcv["data"]["attributes"]["ohlcv_list"][-1][0]
    candles = parse_kline(tr.kline_response(ohlcv, since=first)).items
    assert len(candles) == len(ohlcv["data"]["attributes"]["ohlcv_list"])
    assert [c.time for c in candles] == sorted(c.time for c in candles)


# ── client relais (réseau simulé) ─────────────────────────────────────────
def fake_fetch(calls: list[str], fail_429: set[str] | None = None) -> Any:
    from memepilot.relay.client import _HttpStatus

    def fetch(url: str, timeout: float) -> Any:
        calls.append(url)
        if fail_429 and any(k in url for k in fail_429):
            raise _HttpStatus(429)
        if "trending_pools" in url:
            return load("gecko_trending.json")
        if "new_pools" in url:
            return load("gecko_new.json")
        if "ohlcv" in url:
            return load("gecko_ohlcv.json")
        if "rugcheck" in url:
            return load("rugcheck_report.json")
        if "dexscreener" in url:
            return load("dexscreener_tokens.json")
        raise AssertionError(url)
    return fetch


def fast(c: RelayClient) -> RelayClient:
    for h in c.hosts.values():
        h.min_interval = 0.0
    return c


async def test_relay_client_serves_gmgn_shapes_and_caches_security() -> None:
    calls: list[str] = []
    c = fast(RelayClient(fetch=fake_fetch(calls)))
    r = await c.call(("market", "trending"), chain="sol", min_liquidity=10_000.0)
    assert parse_trending(r.data).items and r.notices == "relais"
    n_rug = sum("rugcheck" in u for u in calls)
    assert 0 < n_rug <= 10  # budget par liste
    await c.call(("token", "security"), chain="sol", address=TOKEN)
    assert sum("rugcheck" in u for u in calls) == n_rug or TOKEN not in str(calls)  # cache réutilisé
    info = await c.call(("token", "info"), chain="sol", address=TOKEN)
    assert TokenInfo.from_api(info.data).symbol == "BLUE"
    with pytest.raises(GmgnCallFailed):
        await c.call(("track", "smartmoney"), chain="sol", limit=100)


async def test_lists_alternate_and_keep_real_fetch_time() -> None:
    calls: list[str] = []
    c = fast(RelayClient(fetch=fake_fetch(calls)))
    first = await c.call(("market", "trending"), chain="sol")
    with pytest.raises(GmgnCallFailed):  # même cycle : une seule liste GeckoTerminal
        await c.call(("market", "trenches"), chain="sol")
    again = await c.call(("market", "trending"), chain="sol")  # servie depuis le cache…
    assert again.fetched_at == first.fetched_at  # …avec sa VRAIE heure de réception (fraîcheur honnête)
    c._last_list -= 30  # cycle suivant : c'est au tour des nouveaux pools
    await c.call(("market", "trenches"), chain="sol")
    assert sum("new_pools" in u for u in calls) == 1 and sum("trending_pools" in u for u in calls) == 1
    assert TRENDING_PATH != NEW_POOLS_PATH


async def test_relay_429_freezes_only_that_host() -> None:
    c = fast(RelayClient(fetch=fake_fetch([], fail_429={"geckoterminal"})))
    with pytest.raises(GmgnRateLimited):
        await c.call(("market", "trending"), chain="sol")
    assert c.hosts["gecko"].frozen_for > 50 and c.hosts["dexscreener"].frozen_for == 0
    assert (await c.call(("token", "info"), chain="sol", address=TOKEN)).data["symbol"] == "BLUE"


def test_http_refuses_other_hosts() -> None:
    with pytest.raises(GmgnCallFailed):
        _http_get_json("https://evil.example.com/x", 1)
    with pytest.raises(GmgnCallFailed):
        _http_get_json("http://api.dexscreener.com/tokens", 1)  # HTTP non chiffré refusé
    assert all(base.startswith("https://") for base, _ in HOSTS.values())


# ── aiguillage ─────────────────────────────────────────────────────────────
class BannedGmgn(GmgnReadClient):
    def __init__(self) -> None:
        super().__init__("gmgn-cli")
        self.seen: list[tuple[str, ...]] = []

    async def call(self, command: tuple[str, ...], cache_ttl_s: float | None = None,
                   **options: ArgValue) -> GmgnResult:
        self.seen.append(command)
        self.limiter.freeze(30)
        raise GmgnRateLimited("RATE_LIMIT_BANNED — gel 30s", 30)


class OkGmgn(GmgnReadClient):
    async def call(self, command: tuple[str, ...], cache_ttl_s: float | None = None,
                   **options: ArgValue) -> GmgnResult:
        return GmgnResult(command, {"address": TOKEN, "symbol": "GMGN"}, 1.0, "", time.time())


async def test_router_falls_back_then_skips_frozen_gmgn() -> None:
    g = BannedGmgn()
    r = RoutedClient(g, fast(RelayClient(fetch=fake_fetch([]))), mode="auto")
    res = await r.call(("token", "info"), chain="sol", address=TOKEN)
    assert res.data["symbol"] == "BLUE" and r.source_counts == {"gmgn": 0, "relais": 1}
    await r.call(("token", "info"), chain="sol", address=TOKEN)
    assert len(g.seen) == 1  # GMGN gelé : on ne le sollicite plus (ne pas prolonger son bannissement)
    assert r.limiter.frozen_for == 0  # le relais reste disponible → le cycle continue


async def test_router_prefers_gmgn_and_never_relays_smart_money() -> None:
    r = RoutedClient(OkGmgn("gmgn-cli"), fast(RelayClient(fetch=fake_fetch([]))), mode="auto")
    assert (await r.call(("token", "info"), chain="sol", address=TOKEN)).data["symbol"] == "GMGN"
    r2 = RoutedClient(BannedGmgn(), fast(RelayClient(fetch=fake_fetch([]))), mode="auto")
    with pytest.raises(GmgnRateLimited):
        await r2.call(("track", "smartmoney"), chain="sol", limit=100)
    r3 = RoutedClient(OkGmgn("gmgn-cli"), fast(RelayClient(fetch=fake_fetch([]))), mode="relay")
    assert (await r3.call(("token", "info"), chain="sol", address=TOKEN)).data["symbol"] == "BLUE"
    with pytest.raises(ValueError):
        RoutedClient(OkGmgn("gmgn-cli"), mode="nimporte")


def test_best_pair_ignores_pairs_without_liquidity() -> None:
    # Cas réel NIKETYSON : seule pair = courbe Meteora sans liquidité, prix faux de 6 ordres de grandeur
    bad = {"baseToken": {"address": TOKEN, "symbol": "X"}, "priceUsd": "0.00000000004309", "liquidity": None}
    assert tr.best_pair([bad], TOKEN) is None
    assert TokenInfo.from_api(tr.token_info_response(TOKEN, tr.best_pair([bad], TOKEN))).price_usd is None
    zero = {**bad, "liquidity": {"usd": 50000}, "priceUsd": "0"}
    assert tr.best_pair([zero], TOKEN) is None


def test_relay_candles_one_per_cycle_and_paused_after_429() -> None:
    c = fast(RelayClient(fetch=fake_fetch([])))
    assert c.kline_ready()
    c._last_kline = time.monotonic()
    assert not c.kline_ready()  # déjà une série ce cycle
    c._last_kline -= 30
    assert c.kline_ready()
    c.hosts["gecko"].last_429 = time.monotonic()
    assert not c.kline_ready()  # 10 min de pause après un 429 GeckoTerminal
    r = RoutedClient(BannedGmgn(), c, mode="auto")
    assert r.kline_budget(3) == 3  # GMGN pas encore gelé
    r.primary.limiter.freeze(30)
    assert r.kline_budget(3) == 1 and not r.can_serve(("market", "kline"))


async def test_relay_never_mislabels_5min_trending() -> None:
    c = fast(RelayClient(fetch=fake_fetch([])))
    with pytest.raises(GmgnCallFailed):
        await c.call(("market", "trending"), chain="sol", interval="5m")
    r = RoutedClient(BannedGmgn(), c, mode="auto")
    r.primary.limiter.freeze(30)
    with pytest.raises(GmgnRateLimited):  # pas relayée : échec franc, la liste 5 min est facultative
        await r.call(("market", "trending"), chain="sol", interval="5m")


# ── D-036 : les prix des positions passent par le relais, le quota GMGN reste au scan et au smart money ──
async def test_position_quotes_use_relay_first_and_spare_gmgn() -> None:
    from memepilot.gmgn.read_client import GmgnResult
    from memepilot.relay.router import RoutedClient

    class Primary:
        def __init__(self) -> None:
            self.calls = 0
            self.limiter = type("L", (), {"frozen_for": 0.0})()

        async def call(self, command, cache_ttl_s=None, **options):  # type: ignore[no-untyped-def]
            self.calls += 1
            return GmgnResult(command, {"price": {"price": "2"}}, 1.0, "", 0.0)

    class Relay:
        def __init__(self, ok: bool) -> None:
            self.ok, self.calls = ok, 0
            self.limiter = type("L", (), {"frozen_for": 0.0})()
            self.http_calls: dict[str, int] = {}

        async def call(self, command, cache_ttl_s=None, **options):  # type: ignore[no-untyped-def]
            from memepilot.gmgn.read_client import GmgnCallFailed
            self.calls += 1
            if not self.ok:
                raise GmgnCallFailed("relais indisponible")
            return GmgnResult(command, {"price": {"price": "1"}}, 1.0, "", 0.0)

    prim, rel = Primary(), Relay(ok=True)
    r = RoutedClient(prim, rel, "auto")  # type: ignore[arg-type]
    res = await r.quote("X")
    assert res.data["price"]["price"] == "1" and prim.calls == 0 and r.source_counts["relais"] == 1
    prim2, rel2 = Primary(), Relay(ok=False)  # relais en panne : GMGN prend le relais
    res = await RoutedClient(prim2, rel2, "auto").quote("X")  # type: ignore[arg-type]
    assert res.data["price"]["price"] == "2" and prim2.calls == 1
    prim3 = Primary()  # mode gmgn : on ne touche pas au relais
    await RoutedClient(prim3, Relay(ok=True), "gmgn").quote("X")  # type: ignore[arg-type]
    assert prim3.calls == 1


def test_limiter_burst_is_capped_by_safety() -> None:
    from memepilot.gmgn.rate_limiter import RateLimiter
    lim = RateLimiter("free", safety=0.8)
    assert lim.burst == 4.0 and lim.rate == 4.0 and lim.capacity == 5.0
