import json
from decimal import Decimal
from pathlib import Path

import pytest

from memepilot.gmgn.models import TokenInfo
from memepilot.market.freshness import TTL_S, Fresh, StaleDataError
from memepilot.market.prices import (
    LAMPORTS_PER_SOL,
    PriceError,
    lamports_to_usd,
    usd_to_lamports,
    validate_sol_price,
)

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "gmgn"


def test_fresh_value_readable_then_stale() -> None:
    f = Fresh.of(42, fetched_at=1000.0, kind="trending")
    assert f.get(now=1000.0 + TTL_S["trending"] - 1) == 42
    with pytest.raises(StaleDataError):
        f.get(now=1000.0 + TTL_S["trending"] + 1)


def test_future_timestamp_beyond_tolerance_is_rejected() -> None:
    f = Fresh.of(1, fetched_at=1100.0, kind="sol_price")
    with pytest.raises(StaleDataError):
        f.get(now=1000.0)


def test_unknown_kind_refused() -> None:
    with pytest.raises(KeyError):
        Fresh.of(1, 0.0, "inconnu")


def test_sol_price_from_real_fixture() -> None:
    info = TokenInfo.from_api(json.loads((FIX / "token_info_sol.json").read_text(encoding="utf-8")))
    p = validate_sol_price(info)
    assert 1 < p < 10_000


@pytest.mark.parametrize(
    "override",
    [{"price_usd": None}, {"price_usd": 0.5}, {"price_usd": 50_000.0}, {"price_usd": 100.0, "price_1m": 80.0}],
)
def test_sol_price_sanity(override: dict[str, float | None]) -> None:
    base = {"address": "So11111111111111111111111111111111111111112", "price_usd": 100.0, "price_1m": 100.0}
    with pytest.raises(PriceError):
        validate_sol_price(TokenInfo.model_validate({**base, **override}))


def test_usd_lamports_conversion_rounds_down() -> None:
    lam = usd_to_lamports(Decimal("5"), 96.0)
    assert lam == 52_083_333  # 5 / 96 SOL, arrondi vers le bas
    assert lamports_to_usd(lam, 96.0) <= Decimal("5")
    assert lamports_to_usd(LAMPORTS_PER_SOL, 96.0) == Decimal("96.0000")
    with pytest.raises(ValueError):
        usd_to_lamports(Decimal("0"), 96.0)
