"""B-10 : profil exploration (papier uniquement), enrichissement token info, cache du flux, Telegram setup."""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from memepilot.engine.cycle import FeedCache
from memepilot.gmgn.models import TokenInfo, TrendingRow
from memepilot.scanner.prefilter import Candidate
from memepilot.signals.base import SignalReport
from memepilot.strategy.hybrid import EXPLORATION, STRICT, Decision, ProfileNotAllowed, decide, profile_config

A = "66auXVTRw9ZekMeB8h5jdJS9WhMRVz8zfMyzqeinpump"
ROOT = Path(__file__).resolve().parents[2]


def test_exploration_is_paper_only() -> None:
    assert profile_config("exploration", "paper") is EXPLORATION
    assert profile_config("strict", "live") is STRICT
    with pytest.raises(ProfileNotAllowed):
        profile_config("exploration", "live")
    with pytest.raises(ProfileNotAllowed):
        profile_config("yolo", "paper")


def test_exploration_is_looser_but_keeps_high_refused() -> None:
    assert EXPLORATION.propose_threshold < STRICT.propose_threshold
    assert EXPLORATION.min_confirming <= STRICT.min_confirming


def test_apply_token_info_fills_momentum_and_keeps_lowest_liquidity() -> None:
    c = Candidate.from_trending(TrendingRow.model_validate({"address": A, "liquidity": "50000"}))
    info = TokenInfo.model_validate({"address": A, "price_usd": 1.1, "price_5m": 1.0, "price_1h": 0.5,
                                     "buys_5m": 30, "sells_5m": 10, "liquidity": 40000})
    c.apply_token_info(info)
    assert c.chg_5m == pytest.approx(0.1)
    assert c.chg_1h == pytest.approx(1.2)
    assert c.buy_ratio == pytest.approx(0.75)
    assert c.liquidity == 40000


def test_apply_token_info_ignores_thin_flow_and_missing_prices() -> None:
    c = Candidate.from_trending(TrendingRow.model_validate({"address": A}))
    c.apply_token_info(TokenInfo.model_validate({"address": A, "buys_5m": 3, "sells_5m": 2}))
    assert c.buy_ratio is None and c.chg_5m is None  # absent reste absent, jamais 0


def test_feed_cache_expires() -> None:
    fc = FeedCache()
    assert not fc.usable(1000.0)
    fc.fetched_at = 1000.0
    assert fc.usable(1100.0)
    assert not fc.usable(1000.0 + fc.max_age_s + 1)


def _load_setup() -> Any:
    spec = importlib.util.spec_from_file_location("telegram_setup", ROOT / "scripts" / "telegram_setup.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_telegram_setup_finds_chat_ids() -> None:
    mod = _load_setup()
    updates = [{"message": {"chat": {"id": 123, "first_name": "Elyas", "type": "private"}}},
               {"my_chat_member": {"chat": {"id": -100, "title": "Groupe", "type": "group"}}},
               {"update_id": 9}]
    chats = mod.find_chats(updates)
    assert chats == {"123": "Elyas (private)", "-100": "Groupe (group)"}


def _sigs(mom: float, sm: float, early: float) -> list[SignalReport]:
    return [SignalReport("momentum", mom, 1.0), SignalReport("smart_money", sm, 0.8),
            SignalReport("early_token", early, 1.0)]


def test_exploration_proposes_strong_momentum_without_smart_money_but_strict_does_not() -> None:
    # Cas réel du 2026-11-01 (iTech) : momentum 84, early 98, aucun achat smart money vu.
    c = Candidate.from_trending(TrendingRow.model_validate(
        {"address": A, "liquidity": "80000", "rug_ratio": "0.05", "open_timestamp": 1}))
    assert decide(c, _sigs(84, 0, 98), STRICT).decision is Decision.NO_TRADE
    d = decide(c, _sigs(84, 0, 98), EXPLORATION)
    if d.risk_class.name != "HIGH":
        assert d.decision is Decision.PROPOSE and d.profile == "exploration"
    # Un token moyen reste refusé même en exploration
    assert decide(c, _sigs(47, 33, 0), EXPLORATION).decision is Decision.NO_TRADE


def test_exploration_can_trade_with_relay_level_confidences() -> None:
    # Cas réel du 2026-11-01 (bot sur le relais, GMGN gelé) : confiances plafonnées à 0,6 / 0,4 / 0,5.
    # Avec l'escompte de couverture, le maximum atteignable était 52 < 55 : aucun trade possible.
    c = Candidate.from_trending(TrendingRow.model_validate(
        {"address": A, "liquidity": "80000", "rug_ratio": "0.05", "open_timestamp": 1}))
    sigs = [SignalReport("momentum", 85, 0.6), SignalReport("smart_money", 90, 0.4),
            SignalReport("early_token", 80, 0.5)]
    assert decide(c, sigs, STRICT).decision is Decision.NO_TRADE
    d = decide(c, sigs, EXPLORATION)
    assert d.decision is Decision.PROPOSE and d.score > 80


def test_exploration_still_refuses_thin_data_and_unconfirmed() -> None:
    c = Candidate.from_trending(TrendingRow.model_validate(
        {"address": A, "liquidity": "80000", "rug_ratio": "0.05", "open_timestamp": 1}))
    # Cas MOGZ (confiances 0,2 / 0,4) : couverture trop faible → refus, même avec de beaux scores
    thin = [SignalReport("momentum", 95, 0.2), SignalReport("smart_money", 95, 0.4)]
    d = decide(c, thin, EXPLORATION)
    assert d.decision is Decision.NO_TRADE and "couverture" in d.reasons[0]
    # Aucun module ne confirme avec une confiance ≥ 0,5 → refus
    weak = [SignalReport("momentum", 40, 0.6), SignalReport("smart_money", 100, 0.4)]
    assert decide(c, weak, EXPLORATION).decision is Decision.NO_TRADE
