import json
from pathlib import Path
from typing import Any

import pytest

from memepilot.gmgn.models import Candle, TrackedTrade, TrendingRow, parse_kline
from memepilot.gmgn.read_client import unwrap
from memepilot.scanner.prefilter import Candidate
from memepilot.signals.early_token import early_token_signal
from memepilot.signals.momentum import candle_features, momentum_signal
from memepilot.signals.smart_money import build_flows, smart_money_signal

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "gmgn"
A = "66auXVTRw9ZekMeB8h5jdJS9WhMRVz8zfMyzqeinpump"
W1 = "DYAn4XpAkN5mhiXkRB7dGq4Jadnx6XYgu8L5b3WGhbrt"
W2 = "2rmaFtFZmJtLXdPgRvk417RLc9GBD3jGbAqtttmfG3AF"
W3 = "4euyaqBe45J5dniAcNARERUo7B1wG96riG21sEpNndfU"
NOW = 1_800_000_000.0


def cand(**over: Any) -> Candidate:
    base: dict[str, Any] = {
        "address": A, "symbol": "T", "liquidity": 60_000, "rug_ratio": 0.05, "renounced_mint": 1,
        "renounced_freeze_account": 1, "is_wash_trading": False, "top_10_holder_rate": 0.15,
        "dev_team_hold_rate": 0, "bundler_rate": 0.05, "rat_trader_amount_rate": 0.01,
        "smart_degen_count": 4, "renowned_count": 1, "buys": 65, "sells": 35,
        "price_change_percent5m": 8, "price_change_percent1h": 30,
    }
    base.update(over)
    return Candidate.from_trending(TrendingRow.model_validate(base))


def candles(closes: list[float], last_high: float | None = None) -> list[Candle]:
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        h = max(o, c) if (last_high is None or i < len(closes) - 1) else last_high
        out.append(Candle(time=1000 + 60 * i, open=o, high=h, low=min(o, c), close=c))
    return out


def trade(maker: str, side: str, age_s: float, usd: float = 500.0, token: str = A) -> TrackedTrade:
    return TrackedTrade.model_validate({"transaction_hash": f"{maker}{age_s}", "maker": maker,
                                        "base_address": token, "side": side, "amount_usd": usd,
                                        "timestamp": int(NOW - age_s)})


# ── momentum ──────────────────────────────────────────────────────────────
def test_momentum_without_data_abstains() -> None:
    c = Candidate.from_trending(TrendingRow.model_validate({"address": A}))
    assert momentum_signal(c).abstains


def test_momentum_row_only_has_reduced_confidence() -> None:
    r = momentum_signal(cand())
    assert 0 < r.confidence <= 0.6 and r.score > 50 and not r.vetoes


@pytest.mark.parametrize(
    ("over", "veto"),
    [({"buys": 30, "sells": 70}, "pression vendeuse"),
     ({"price_change_percent1h": -20, "price_change_percent5m": -8}, "baisse continue")],
)
def test_momentum_vetoes(over: dict[str, Any], veto: str) -> None:
    assert any(veto in v for v in momentum_signal(cand(**over)).vetoes)


def test_candles_raise_confidence_and_detect_fomo_and_wick() -> None:
    steady = candles([1.0 + 0.01 * i for i in range(15)])
    r = momentum_signal(cand(), steady)
    assert r.confidence == 1.0 and not r.vetoes
    pump = candles([1.0] * 10 + [1.2, 1.4, 1.6, 1.8, 2.0])
    assert any("pompe verticale" in v for v in momentum_signal(cand(), pump).vetoes)
    wick = candles([1.0 + 0.01 * i for i in range(15)], last_high=1.5)
    assert any("mèche" in v for v in momentum_signal(cand(), wick).vetoes)
    assert candle_features(steady[:5]) is None


def test_real_kline_fixture_features() -> None:
    cs = parse_kline(unwrap(json.loads((FIX / "market_kline_1m.json").read_text(encoding="utf-8")))).items
    f = candle_features(cs)
    assert f is not None and f["vol"] >= 0


# ── smart money ───────────────────────────────────────────────────────────
def test_flows_count_distinct_wallets_and_decay() -> None:
    flows = build_flows([trade(W1, "buy", 60), trade(W1, "buy", 120), trade(W2, "buy", 1200),
                         trade(W3, "buy", 4000), trade(W2, "buy", 30, usd=10)], NOW)
    f = flows[A]
    assert f.buyers == {W1, W2}  # W3 hors fenêtre, poussière ignorée
    assert 1.0 < f.weighted_buyers < 2.0  # W2 a 20 min → poids ~0,25


def test_smart_money_exit_veto() -> None:
    flows = build_flows([trade(W1, "buy", 60), trade(W2, "sell", 60), trade(W3, "sell", 90)], NOW)
    r = smart_money_signal(cand(), flows[A], feed_available=True)
    assert any("sortie smart money" in v for v in r.vetoes)


def test_smart_money_feed_unavailable_lowers_confidence() -> None:
    r = smart_money_signal(cand(), None, feed_available=False)
    assert r.confidence <= 0.4


def test_smart_money_cluster_scores_high() -> None:
    flows = build_flows([trade(w, "buy", 30) for w in (W1, W2, W3)], NOW, kol_makers={W1})
    r = smart_money_signal(cand(), flows[A], feed_available=True)
    assert r.score >= 70 and r.confidence == 1.0 and not r.vetoes


# ── early token ───────────────────────────────────────────────────────────
def _early(**over: Any) -> Candidate:
    c = cand()
    c.age_min = 60
    c.creator_open_ratio, c.creator_created_count = 0.4, 3
    c.insider_rate, c.sniper_hold, c.fresh_wallet_rate, c.bot_degen_rate = 0.02, 0.03, 0.05, 0.1
    for k, v in over.items():
        setattr(c, k, v)
    return c


def test_early_not_applicable_to_old_or_unknown_age() -> None:
    assert early_token_signal(_early(age_min=3000)).abstains
    assert early_token_signal(_early(age_min=None)).abstains


def test_early_clean_token_scores_high() -> None:
    r = early_token_signal(_early())
    assert r.score >= 75 and r.confidence == 1.0 and not r.vetoes


def test_early_token_factory_vetoed() -> None:
    assert early_token_signal(_early(creator_created_count=600)).vetoes
    assert early_token_signal(_early(creator_created_count=40, creator_open_ratio=0.01)).vetoes


def test_early_missing_creator_data_lowers_confidence() -> None:
    r = early_token_signal(_early(creator_open_ratio=None, creator_created_count=None))
    assert r.confidence < 0.8


# ── D-038 : smart money = bonus seulement ──
def test_smart_money_absence_is_neutral_not_zero() -> None:
    c = cand()
    c.smart_degen, c.renowned = 0, 0
    for feed in (False, True):  # flux coupé, ou flux OK mais aucun achat récent
        r = smart_money_signal(c, None, feed_available=feed)
        assert r.abstains and not r.vetoes
    # Revente sans aucun acheteur : toujours un veto
    flows = build_flows([trade(W1, "sell", 30)], NOW)
    assert smart_money_signal(c, flows[A], feed_available=True).vetoes


def test_absent_smart_money_no_longer_drags_the_hybrid_score() -> None:
    from memepilot.signals.base import SignalReport
    from memepilot.strategy.hybrid import EXPLORATION, decide
    c = cand()
    c.smart_degen, c.renowned = 0, 0
    mom = SignalReport("momentum", 65.7, 1.0, (), ())
    early = SignalReport("early_token", 81.0, 1.0, (), ())
    sm = smart_money_signal(c, None, feed_available=True)
    d = decide(c, [mom, sm, early], EXPLORATION)
    assert d.score > 70  # cas Leverhedge : 54 avant D-038 (0 pondéré), ~72 maintenant
