from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from memepilot.config.limits import Limits, load_limits
from memepilot.risk.guardian import BuyRequest, budgets, evaluate
from memepilot.risk.state import (
    StateError,
    cancel_reservation,
    close_position,
    fill_buy,
    new_state,
    reserve,
    roll_day,
    set_halt,
)

ROOT = Path(__file__).resolve().parents[2]
LIM: Limits = load_limits(ROOT / "tests" / "fixtures" / "limits_test.yaml")
TODAY = date(2026, 11, 2)
FEES = D("0.40")


def state():  # type: ignore[no-untyped-def]
    return new_state(LIM.initial_capital_usd, TODAY, LIM.fingerprint())


def req(token: str = "T1", **over: object) -> BuyRequest:
    base = dict(token=token, size_fraction=D("1"), security_passed=True, data_fresh=True,
                liquidity_usd=D("50000"), est_round_trip_fees_usd=FEES, wallet_balance_usd=D("50"),
                ledger_balance_usd=D("50"))
    base.update(over)
    return BuyRequest(**base)  # type: ignore[arg-type]


def buy(s, token: str, size: D):  # type: ignore[no-untyped-def]
    s = reserve(s, token, size + FEES)
    return fill_buy(s, token, size, FEES / 2, FEES / 2)


# ── cas nominal et taille au pire cas ────────────────────────────────────
def test_fresh_state_first_trade_is_sized_by_daily_budget() -> None:
    d = evaluate(req(), state(), LIM, TODAY)
    # budget jour 5 $ − frais 0,40 $ → 4,60 $ (et non 5 $ : la perte max inclut les frais)
    assert d.approved and d.size_usd == D("4.60")


def test_open_position_consumes_budget_d010() -> None:
    s = buy(state(), "T1", D("4.60"))  # exposition 5,00 $
    d = evaluate(req("T2"), s, LIM, TODAY)
    assert not d.approved and any("budget insuffisant" in r for r in d.reasons)


def test_two_half_positions_fit() -> None:
    s = state()
    d1 = evaluate(req("T1", size_fraction=D("0.5")), s, LIM, TODAY)
    assert d1.approved and d1.size_usd == D("2.50")
    s = buy(s, "T1", d1.size_usd)  # exposition 2,90 $
    d2 = evaluate(req("T2", size_fraction=D("0.5")), s, LIM, TODAY)
    assert not d2.approved  # 5 − 2,90 − 0,40 = 1,70 $ < minimum 2 $


def test_loss_today_shrinks_budget_then_blocks() -> None:
    s = buy(state(), "T1", D("4.60"))
    s = close_position(s, "T1", D("2.00"), D("0.20"), LIM.maximum_cumulative_loss_usd)  # perte 3,00 $
    assert s.daily_loss_usd == D("3.00")
    d = evaluate(req("T2"), s, LIM, TODAY)
    # 5 − 3 − 0,40 = 1,60 $ possibles < minimum 2 $ → refus
    assert not d.approved and any("1.60" in r for r in d.reasons)


def test_new_day_restores_daily_budget_not_cumulative() -> None:
    s = buy(state(), "T1", D("4.60"))
    s = close_position(s, "T1", D("0"), D("0.20"), LIM.maximum_cumulative_loss_usd)  # perte 5,00 $
    tomorrow = date(2026, 11, 3)
    s2 = roll_day(s, tomorrow)
    assert s2.daily_loss_usd == 0 and s2.cumulative_loss_usd == D("5.00")
    assert evaluate(req(), s2, LIM, tomorrow).approved


def test_gains_do_not_restore_cumulative_budget() -> None:
    s = buy(state(), "T1", D("4.60"))
    s = close_position(s, "T1", D("0"), D("0.20"), LIM.maximum_cumulative_loss_usd)  # −5
    s = roll_day(s, date(2026, 11, 3))
    s = buy(s, "T2", D("4.60"))
    s = close_position(s, "T2", D("14.60"), D("0.20"), LIM.maximum_cumulative_loss_usd)  # +9,60
    assert s.realized_equity_usd > LIM.initial_capital_usd
    assert s.cumulative_loss_usd == D("5.00")  # le pire point est retenu


def test_cumulative_limit_locks_and_blocks() -> None:
    s = state()
    day = TODAY
    for i in range(4):
        s = buy(s, f"T{i}", D("4.60"))
        s = close_position(s, f"T{i}", D("0"), D("0.20"), LIM.maximum_cumulative_loss_usd)
        day = date(2026, 11, 3 + i)
        s = roll_day(s, day)
    assert s.cumulative_loss_usd == D("20.00") and s.locked
    d = evaluate(req("X"), s, LIM, day)
    assert not d.approved and any("LOCK" in r for r in d.reasons)


# ── chaque règle de veto ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"security_passed": False}, "sécurité"),
        ({"data_fresh": False}, "périmées"),
        ({"liquidity_usd": None}, "liquidité inconnue"),
        ({"liquidity_usd": D("5000")}, "liquidité"),
        ({"est_round_trip_fees_usd": None}, "frais estimés inconnus"),
        ({"wallet_balance_usd": None}, "solde"),
        ({"wallet_balance_usd": D("45")}, "écart solde/ledger"),
        ({"size_fraction": D("1.5")}, "fraction"),
        ({"size_fraction": D("0")}, "fraction"),
    ],
)
def test_request_vetoes(over: dict[str, object], reason: str) -> None:
    d = evaluate(req(**over), state(), LIM, TODAY)
    assert not d.approved and any(reason in r for r in d.reasons) and d.size_usd == 0


def test_state_vetoes() -> None:
    s = state()
    assert not evaluate(req(), set_halt(s, True, "test"), LIM, TODAY).approved
    assert not evaluate(req(), replace(s, limits_fingerprint="autre"), LIM, TODAY).approved
    assert not evaluate(req(), s, LIM, date(2026, 11, 3)).approved  # jour non basculé
    assert not evaluate(req(), replace(s, trades_today=10), LIM, TODAY).approved


def test_duplicate_and_pending_order_blocked() -> None:
    s = reserve(state(), "T1", D("2.4"))
    d = evaluate(req("T1"), s, LIM, TODAY)
    assert any("non résolu" in r for r in d.reasons)
    with pytest.raises(StateError):
        reserve(s, "T1", D("1"))


def test_concurrency_limit_counts_pending() -> None:
    lim = Limits.model_validate({**LIM.model_dump(), "daily_loss_limit_usd": "10", "max_position_usd": "5"})
    s = new_state(lim.initial_capital_usd, TODAY, lim.fingerprint())
    s = reserve(reserve(s, "A", D("1")), "B", D("1"))
    d = evaluate(req("C"), s, lim, TODAY)
    assert any("max 2" in r for r in d.reasons)


def test_failed_order_releases_reservation_without_counting_trade() -> None:
    s = reserve(state(), "T1", D("5"))
    assert budgets(s, LIM)[0] == D("0")
    s = cancel_reservation(s, "T1")
    assert s.trades_today == 0 and budgets(s, LIM)[0] == D("5")


def test_invalid_transitions() -> None:
    s = state()
    with pytest.raises(StateError):
        fill_buy(s, "X", D("1"), D("0"), D("0"))  # achat sans réservation
    with pytest.raises(StateError):
        close_position(s, "X", D("1"), D("0"), D("20"))
    with pytest.raises(StateError):
        roll_day(s, date(2026, 1, 1))  # retour dans le passé


def test_owner_config_d033_two_positions_of_5_usd() -> None:
    """Config RÉELLE (D-033 : 5 $ min et max, 15 $/jour, 20 $ au total) : deux positions de 5 $, pas trois."""
    real = load_limits(ROOT / "config" / "limits.yaml")
    assert real.daily_loss_limit_usd == D("15") and real.maximum_cumulative_loss_usd == D("20")
    assert real.min_position_usd == real.max_position_usd == D("5")
    s = new_state(real.initial_capital_usd, TODAY, real.fingerprint())
    d1 = evaluate(req("T1"), s, real, TODAY)
    s = buy(s, "T1", d1.size_usd)
    d2 = evaluate(req("T2"), s, real, TODAY)
    assert d1.size_usd == d2.size_usd == D("5.00")  # 15 − 5,40 − 0,40 = 9,20 $ disponibles
    s = buy(s, "T2", d2.size_usd)
    assert not evaluate(req("T3"), s, real, TODAY).approved  # plafond de 2 positions
    # Jamais moins de 5 $ : une demi-taille est refusée, et le budget du jour doit couvrir 5 $ + frais
    assert not evaluate(req("T4", size_fraction=D("0.5")), new_state(real.initial_capital_usd, TODAY,
                                                                      real.fingerprint()), real, TODAY).approved
