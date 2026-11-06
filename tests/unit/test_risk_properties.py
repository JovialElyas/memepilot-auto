"""Tests de propriétés (Hypothesis) : AUCUNE séquence d'événements ne viole les limites.

Le simulateur joue des milliers de journées aléatoires : demandes d'achat (tailles, fractions,
frais variables), ordres qui échouent, ventes à n'importe quel prix (y compris 0 = rug), changements
de jour. Seules les demandes APPROUVÉES par le Guardian sont exécutées.
Hypothèse documentée : les frais réels ne dépassent pas les frais estimés (sinon la perte peut
dépasser la limite du montant de l'écart — d'où une estimation prudente, Q-003).
"""

from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from memepilot.config.limits import load_limits
from memepilot.risk.guardian import BuyRequest, evaluate
from memepilot.risk.state import cancel_reservation, close_position, fill_buy, new_state, reserve, roll_day

LIM = load_limits(Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "limits_test.yaml")
START = date(2026, 11, 2)

money = st.decimals(min_value=D("0"), max_value=D("30"), places=2)
fees = st.decimals(min_value=D("0.05"), max_value=D("1.50"), places=2)

action = st.one_of(
    st.tuples(st.just("buy"), st.integers(0, 6), st.sampled_from([D("1"), D("0.5")]), fees,
              st.booleans(), st.decimals(min_value=D("0"), max_value=D("1"), places=2)),
    st.tuples(st.just("sell"), st.integers(0, 6), st.decimals(min_value=D("0"), max_value=D("3"), places=2)),
    st.tuples(st.just("day")),
)


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(action, min_size=1, max_size=120))
def test_limits_never_breached(actions: list[tuple]) -> None:  # type: ignore[type-arg]
    s = new_state(LIM.initial_capital_usd, START, LIM.fingerprint())
    today = START
    est_fee: dict[str, D] = {}
    for a in actions:
        if a[0] == "day":
            today += timedelta(days=1)
            s = roll_day(s, today)
        elif a[0] == "buy":
            _, i, frac, fee, order_fails, fee_ratio = a
            tok = f"T{i}"
            wallet = s.realized_equity_usd - sum((p.cost_usd + p.entry_fee_usd for p in s.positions), D(0))
            d = evaluate(BuyRequest(tok, frac, True, True, D("50000"), fee, wallet, wallet), s, LIM, today)
            if not d.approved:
                continue
            assert d.size_usd <= LIM.max_position_usd
            assert d.size_usd >= LIM.min_position_usd
            s = reserve(s, tok, d.size_usd + fee)
            if order_fails:
                s = cancel_reservation(s, tok)
                continue
            entry = (fee * fee_ratio).quantize(D("0.01"))  # frais réels ≤ estimés
            s = fill_buy(s, tok, d.size_usd, entry, fee - entry)
            est_fee[tok] = fee - entry
        else:
            _, i, mult = a
            tok = f"T{i}"
            pos = next((p for p in s.positions if p.token == tok), None)
            if pos is None:
                continue
            proceeds = (pos.cost_usd * mult).quantize(D("0.01"))  # 0 = rug total
            s = close_position(s, tok, proceeds, est_fee[tok], LIM.maximum_cumulative_loss_usd)

        # ── invariants ─────────────────────────────────────────────────
        assert s.daily_loss_usd <= LIM.daily_loss_limit_usd
        assert s.cumulative_loss_usd <= LIM.maximum_cumulative_loss_usd
        # même si TOUT ce qui est ouvert tombe à zéro maintenant :
        assert s.daily_loss_usd + s.open_exposure_usd <= LIM.daily_loss_limit_usd
        assert s.cumulative_loss_usd + s.open_exposure_usd <= LIM.maximum_cumulative_loss_usd
        assert s.slots_used <= LIM.max_concurrent_positions
        assert s.trades_today <= LIM.max_trades_per_day
        assert s.realized_equity_usd >= LIM.initial_capital_usd - LIM.maximum_cumulative_loss_usd
        if s.locked:
            assert not evaluate(BuyRequest("Z", D("1"), True, True, D("50000"), D("0.1"), D("50"), D("50")),
                                s, LIM, today).approved


@settings(max_examples=200, deadline=None)
@given(st.lists(st.tuples(fees, st.sampled_from([D("1"), D("0.5")])), min_size=1, max_size=40))
def test_capital_floor_with_only_rugs(seq: list[tuple[D, D]]) -> None:
    """Pire scénario : chaque trade autorisé finit à 0. Le capital ne descend jamais sous 30 $."""
    s = new_state(LIM.initial_capital_usd, START, LIM.fingerprint())
    today = START
    for n, (fee, frac) in enumerate(seq):
        tok = f"R{n}"
        wallet = s.realized_equity_usd
        d = evaluate(BuyRequest(tok, frac, True, True, D("50000"), fee, wallet, wallet), s, LIM, today)
        if d.approved:
            s = fill_buy(reserve(s, tok, d.size_usd + fee), tok, d.size_usd, fee / 2, fee / 2)
            s = close_position(s, tok, D("0"), fee / 2, LIM.maximum_cumulative_loss_usd)
        today += timedelta(days=1)
        s = roll_day(s, today)
        assert s.realized_equity_usd >= D("30")
    assert s.locked or s.cumulative_loss_usd < LIM.maximum_cumulative_loss_usd
