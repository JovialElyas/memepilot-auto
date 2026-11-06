import json
import time
from pathlib import Path
from typing import Any

from memepilot.gmgn.models import TokenPool, TokenSecurity, TrendingRow, parse_trenches
from memepilot.gmgn.read_client import ArgValue, GmgnRateLimited, GmgnReadClient, GmgnResult, unwrap
from memepilot.scanner.pipeline import ScanConfig, scan_once
from memepilot.scanner.prefilter import Candidate, PrefilterConfig, prefilter

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "gmgn"
A = "66auXVTRw9ZekMeB8h5jdJS9WhMRVz8zfMyzqeinpump"
B = "Fp4JkqEcKYKBKhb9iMu9NSDeo71HWgq2q9QtXaaKpump"

SAFE_ROW: dict[str, Any] = {
    "address": A, "symbol": "OK", "liquidity": 50_000, "rug_ratio": 0.05, "renounced_mint": 1,
    "renounced_freeze_account": 1, "is_wash_trading": False, "top_10_holder_rate": 0.15,
    "dev_team_hold_rate": 0, "bundler_rate": 0.05, "rat_trader_amount_rate": 0.01,
    "smart_degen_count": 3, "renowned_count": 1, "buys": 60, "sells": 40,
}


def cand(**over: Any) -> Candidate:
    return Candidate.from_trending(TrendingRow.model_validate({**SAFE_ROW, **over}))


def load(name: str) -> Any:
    return unwrap(json.loads((FIX / name).read_text(encoding="utf-8")))


# ── préfiltre ─────────────────────────────────────────────────────────────
def test_safe_candidate_passes() -> None:
    assert prefilter(cand()).passed


def test_missing_critical_fields_reject() -> None:
    v = prefilter(Candidate.from_trending(TrendingRow.model_validate({"address": A})))
    assert not v.passed
    for r in ("mint:absent", "freeze:absent", "wash_trading:absent", "liquidite:absent", "rug_ratio:absent"):
        assert r in v.reasons


def test_each_threshold_rejects() -> None:
    cases = {
        "rug_ratio": ({"rug_ratio": 0.31}, "rug_ratio:>0.3"),
        "top10": ({"top_10_holder_rate": 0.5}, "top10:>0.3"),
        "liq": ({"liquidity": 9_000}, "liquidite:<10000"),
        "mint": ({"renounced_mint": 0}, "mint_non_renonce"),
        "freeze": ({"renounced_freeze_account": 0}, "freeze_non_renonce"),
        "wash": ({"is_wash_trading": True}, "wash_trading"),
        "bundler": ({"bundler_rate": 0.4}, "bundler:>0.2"),
        "honeypot": ({"is_honeypot": 1}, "honeypot"),
        "top10_zero": ({"top_10_holder_rate": 0}, "top10:zero_suspect"),
    }
    for name, (over, reason) in cases.items():
        v = prefilter(cand(**over))
        assert not v.passed and reason in v.reasons, name


def test_secondary_missing_warns_only() -> None:
    c = cand(bundler_rate=None)
    assert prefilter(c).passed
    assert "bundler:absent" in c.warnings


def test_real_trending_top_token_rejected() -> None:
    # Le n°1 du trending capturé avait rug_ratio 0,98 et bundler 0,31.
    rows = load("market_trending_sol_1h.json")["rank"]
    v = prefilter(Candidate.from_trending(TrendingRow.model_validate(rows[0])))
    assert not v.passed and "rug_ratio:>0.3" in v.reasons


def test_merge_fills_gaps_without_overwrite() -> None:
    a = cand(rug_ratio=0.1, bundler_rate=None)
    t = parse_trenches(load("market_trenches.json")).items[0]
    b = Candidate.from_trenches(t.model_copy(update={"address": A}))
    a.merge(b)
    assert a.rug_ratio == 0.1
    assert a.bundler == b.bundler
    assert "trending" in a.sources and any(s.startswith("trenches") for s in a.sources)


def test_security_source_wins_and_warns() -> None:
    c = cand()
    c.apply_security(TokenSecurity.model_validate({"address": A, "renounced_mint": False}))
    assert c.renounced_mint is False and c.warnings
    assert not prefilter(c).passed


def test_pool_keeps_lowest_liquidity() -> None:
    c = cand(liquidity=50_000)
    c.apply_pool(TokenPool.model_validate({"address": A, "liquidity": "8000"}), PrefilterConfig())
    assert c.liquidity == 8000 and any("incohérente" in w for w in c.warnings)
    assert not prefilter(c).passed


# ── pipeline complet avec un faux client ─────────────────────────────────
class FakeClient(GmgnReadClient):
    def __init__(self, responses: dict[tuple[str, ...], Any], fail: set[tuple[str, ...]] | None = None) -> None:
        super().__init__("gmgn-cli")
        self.responses = responses
        self.fail = fail or set()
        self.seen: list[tuple[tuple[str, ...], dict[str, ArgValue]]] = []

    async def call(self, command: tuple[str, ...], cache_ttl_s: float | None = None,
                   **options: ArgValue) -> GmgnResult:
        self.seen.append((command, options))
        self.calls += 1
        if command in self.fail:
            raise GmgnRateLimited("RATE_LIMIT_BANNED — gel 20s", 20)
        data = self.responses[command]
        if callable(data):
            data = data(options)
        return GmgnResult(command, data, 5.0, "", time.time())


def responses() -> dict[tuple[str, ...], Any]:
    safe_b = {**SAFE_ROW, "address": B, "symbol": "OK2", "smart_degen_count": 9}
    return {
        ("token", "info"): lambda o: load("token_info_sol.json") if o["address"].startswith("So111") else {},
        ("market", "trending"): {"rank": [*load("market_trending_sol_1h.json")["rank"], SAFE_ROW, safe_b]},
        ("market", "trenches"): load("market_trenches.json"),
        ("token", "security"): lambda o: {"address": o["address"], "renounced_mint": 1,
                                          "renounced_freeze_account": 1, "is_honeypot": None},
        ("token", "pool"): lambda o: {"address": o["address"], "liquidity": "48000"},
    }


async def test_pipeline_end_to_end_readonly() -> None:
    client = FakeClient(responses())
    rep = await scan_once(client, ScanConfig(enrich_n=2))
    d = rep.to_dict()
    assert d["decision"].startswith("AUCUNE")
    assert rep.sol_usd and rep.sol_usd > 1
    assert rep.rows_seen > 5 and rep.rejected > 0
    addrs = {c.address for c in rep.survivors}
    assert {A, B} <= addrs
    order = [c.address for c in rep.survivors]
    assert order.index(B) < order.index(A)  # plus de smart money → enrichi en priorité
    sm = [(c.smart_degen or 0) + (c.renowned or 0) for c in rep.survivors]
    assert sm == sorted(sm, reverse=True)
    assert rep.survivors[0].enriched and rep.survivors[1].enriched
    assert "rug_ratio:>0.3" in rep.reject_reasons
    # aucune commande d'exécution n'a été appelée
    assert all(cmd[0] in ("token", "market") for cmd, _ in client.seen)
    # filtres serveur bien transmis
    trending_opts = next(o for c, o in client.seen if c == ("market", "trending"))
    assert trending_opts["min_liquidity"] == 10_000.0
    trenches_opts = next(o for c, o in client.seen if c == ("market", "trenches"))
    assert trenches_opts["max_rug_ratio"] == 0.30


async def test_pipeline_stops_on_rate_limit() -> None:
    client = FakeClient(responses(), fail={("market", "trending")})
    rep = await scan_once(client, ScanConfig())
    assert rep.rate_limited
    # trending / trenches / prix SOL partent ensemble ; après le 429, plus aucun appel (pas d'enrichissement)
    assert not any(c in (("token", "security"), ("token", "pool")) for c, _ in client.seen)
    assert not any(c.enriched for c in rep.survivors)  # le cycle d'analyse s'arrête sur rate_limited


async def test_duplicate_address_keeps_risky_values() -> None:
    """Une ligne « propre » ne doit pas blanchir un token déjà vu avec un rug_ratio élevé."""
    real_top = load("market_trending_sol_1h.json")["rank"][0]
    clean_copy = {**SAFE_ROW, "address": real_top["address"]}
    r = responses()
    r[("market", "trending")] = {"rank": [real_top, clean_copy]}
    rep = await scan_once(FakeClient(r), ScanConfig())
    assert real_top["address"] not in {c.address for c in rep.survivors}


async def test_fast_trending_list_is_optional_and_tagged() -> None:
    from memepilot.gmgn.read_client import GmgnRateLimited as _RL

    class FastFails(FakeClient):
        async def call(self, command: tuple[str, ...], cache_ttl_s: float | None = None,
                       **options: ArgValue) -> GmgnResult:
            if command == ("market", "trending") and options.get("interval") == "5m":
                self.seen.append((command, options))
                raise _RL("RATE_LIMIT_BANNED — gel 20s", 20)
            return await super().call(command, cache_ttl_s, **options)

    client = FastFails(responses())
    rep = await scan_once(client, ScanConfig())
    assert not rep.rate_limited and rep.survivors  # la liste 1 h suffit, le cycle continue
    assert any("facultatif" in e for e in rep.source_errors)
    ok = await scan_once(FakeClient(responses()), ScanConfig())
    assert any(any(s.startswith("trending:5m") for s in c.sources) for c in ok.survivors)
