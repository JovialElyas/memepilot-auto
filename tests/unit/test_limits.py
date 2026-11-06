from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from memepilot.config.limits import Limits, LimitsError, load_limits

ROOT = Path(__file__).resolve().parents[2]

BASE = dict(
    initial_capital_usd="50",
    maximum_cumulative_loss_usd="20",
    daily_loss_limit_usd="5",
    max_position_usd="5",
    min_position_usd="2",
    max_concurrent_positions=2,
    max_trades_per_day=10,
    max_slippage_pct=30,
    max_order_retries=1,
    leverage=False,
    automatic_reloading=False,
    external_withdrawals=False,
)


def make(**over: object) -> Limits:
    return Limits.model_validate({**BASE, **over})


def test_project_config_is_valid() -> None:
    limits = load_limits(ROOT / "config" / "limits.yaml")
    assert limits.maximum_cumulative_loss_usd == Decimal("20")
    assert limits.max_position_usd <= limits.daily_loss_limit_usd


def test_limits_are_frozen() -> None:
    limits = make()
    with pytest.raises(ValidationError):
        limits.maximum_cumulative_loss_usd = Decimal("100")  # type: ignore[misc]


@pytest.mark.parametrize(
    "override",
    [
        {"maximum_cumulative_loss_usd": "20.01"},  # au-dessus du plafond absolu
        {"maximum_cumulative_loss_usd": "60"},
        {"daily_loss_limit_usd": "25"},
        {"min_position_usd": "6"},
        {"max_position_usd": "6"},  # D-010 : > perte journalière
        {"leverage": True},
        {"automatic_reloading": True},
        {"external_withdrawals": True},
        {"max_order_retries": 3},
        {"max_slippage_pct": 80},
        {"unknown_field": 1},
        {"initial_capital_usd": "-1"},
    ],
)
def test_invalid_limits_rejected(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make(**override)


def test_stricter_limits_allowed() -> None:
    limits = make(maximum_cumulative_loss_usd="10", daily_loss_limit_usd="3", max_position_usd="3")
    assert limits.maximum_cumulative_loss_usd == Decimal("10")


def test_fingerprint_stable_and_sensitive() -> None:
    assert make().fingerprint() == make().fingerprint()
    assert make().fingerprint() != make(max_trades_per_day=9).fingerprint()


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(LimitsError):
        load_limits(tmp_path / "nope.yaml")


def test_yaml_floats_are_exact(tmp_path: Path) -> None:
    p = tmp_path / "l.yaml"
    content = (ROOT / "config" / "limits.yaml").read_text(encoding="utf-8")
    p.write_text(content.replace("min_position_usd: 5 ", "min_position_usd: 4.1 "), encoding="utf-8")
    assert load_limits(p).min_position_usd == Decimal("4.1")
