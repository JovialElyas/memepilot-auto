import json
from pathlib import Path
from typing import Any

import pytest

from memepilot.gmgn.models import (
    Candle,
    TokenInfo,
    TokenPool,
    TokenSecurity,
    TrendingRow,
    parse_bound_wallets,
    parse_holders,
    parse_kline,
    parse_tracked_trades,
    parse_trenches,
    parse_trending,
)
from memepilot.gmgn.read_client import unwrap

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "gmgn"
ADDR = "Df7kaKskbRMAYEkPEGJ1e33n4RPWDNs9GP9pzPjgpump"


def load(name: str) -> Any:
    return unwrap(json.loads((FIX / name).read_text(encoding="utf-8")))


# ── fixtures réelles : 100 % parsées ──────────────────────────────────────
def test_trending_fixture_parses() -> None:
    rep = parse_trending(load("market_trending_sol_1h.json"))
    assert rep.ok and len(rep.items) == 5
    r = rep.items[0]
    assert r.rug_ratio == pytest.approx(0.98)
    assert r.renounced_mint is True
    assert r.price_change_percent1h is not None
    assert r.chg_1h == pytest.approx(r.price_change_percent1h / 100)  # 962 % → 9.62 (fraction)
    assert r.buy_ratio is not None and 0 < r.buy_ratio < 1


def test_trenches_fixture_parses() -> None:
    rep = parse_trenches(load("market_trenches.json"))
    assert rep.ok and rep.items
    assert all(i.category == "completed" for i in rep.items)
    assert all(i.rug_ratio is not None for i in rep.items)


def test_token_endpoints_parse() -> None:
    sec = TokenSecurity.from_api(load("token_security.json"))
    assert sec.renounced_mint is True and sec.renounced_freeze_account is True
    assert sec.is_honeypot is None  # Solana : non fourni — et surtout PAS False
    assert sec.top_10_holder_rate == pytest.approx(0.2485)
    assert sec.lock_percent == pytest.approx(0.95)
    pool = TokenPool.model_validate(load("token_pool.json"))
    assert pool.liquidity and pool.liquidity > 0
    info = TokenInfo.from_api(load("token_info.json"))
    assert info.price_usd and info.price_usd > 0
    assert info.creator_address and info.top_10_holder_rate is not None
    assert not info.is_unknown


def test_unknown_token_detected() -> None:
    info = TokenInfo.from_api(load("token_info_unknown.json"))
    assert info.is_unknown


def test_kline_ms_to_seconds_and_sorted() -> None:
    rep = parse_kline(load("market_kline_1m.json"))
    assert rep.ok and len(rep.items) == 30
    assert all(c.time < 10**11 for c in rep.items)
    assert [c.time for c in rep.items] == sorted(c.time for c in rep.items)


def test_holders_trades_wallets_parse() -> None:
    assert parse_holders(load("token_holders.json")).ok
    kol = parse_tracked_trades(load("track_kol.json"))
    assert kol.ok and kol.items[0].side in ("buy", "sell") and kol.items[0].maker_tags
    sm = parse_tracked_trades(load("track_smartmoney.json"))
    assert sm.ok
    w = parse_bound_wallets({"wallets": [{"chain": "sol", "address": ADDR, "balance": "1"}]})
    assert w.items[0].address == ADDR
    assert parse_bound_wallets(load("portfolio_info_demo.json")).items == []


# ── règle centrale : absent = None, jamais 0 / False ─────────────────────
def test_missing_fields_are_none_not_safe_defaults() -> None:
    r = TrendingRow.model_validate({"address": ADDR})
    assert r.rug_ratio is None
    assert r.is_honeypot is None
    assert r.renounced_mint is None
    assert r.bundler_rate is None
    assert r.buy_ratio is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0.2485", 0.2485), (1, 1.0), ("", None), (None, None), ("nan", None)],
)
def test_float_normalization(raw: Any, expected: float | None) -> None:
    assert TrendingRow.model_validate({"address": ADDR, "rug_ratio": raw}).rug_ratio == expected


@pytest.mark.parametrize(
    ("raw", "expected"), [(1, True), (0, False), ("1", True), ("false", False), (None, None), (True, True)]
)
def test_bool_normalization(raw: Any, expected: bool | None) -> None:
    assert TrendingRow.model_validate({"address": ADDR, "renounced_mint": raw}).renounced_mint is expected


def test_bad_rows_reported_not_silently_fixed() -> None:
    rows = [
        {"address": ADDR, "rug_ratio": "0.1"},
        {"address": "pas-une-adresse"},
        {"address": ADDR, "rug_ratio": "beaucoup"},
        "pas un objet",
    ]
    rep = parse_trending({"rank": rows})
    assert len(rep.items) == 1
    assert len(rep.errors) == 3


def test_candle_rejects_zero_price() -> None:
    with pytest.raises(ValueError):
        Candle.model_validate({"time": 1, "open": "0", "high": "1", "low": "1", "close": "1"})


def test_tracked_trade_rejects_unknown_side() -> None:
    rep = parse_tracked_trades({"list": [{
        "transaction_hash": "x", "maker": ADDR, "base_address": ADDR, "side": "transfer", "timestamp": 1,
    }]})
    assert not rep.items and rep.errors


def test_unwrap_error_envelope_without_data() -> None:
    from memepilot.gmgn.read_client import GmgnCallFailed

    with pytest.raises(GmgnCallFailed):
        unwrap({"code": 429, "msg": "rate limited"})
    assert unwrap({"code": 0, "data": {"rank": []}}) == {"rank": []}
