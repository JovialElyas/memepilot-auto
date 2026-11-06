from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from memepilot.config.limits import Limits, load_limits
from memepilot.risk.state import close_position, fill_buy, reserve
from memepilot.risk.store import UNLOCK_PHRASE, LimitsChangedError, RiskStore, UnlockRefused

LIM = load_limits(Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "limits_test.yaml")
DAY = date(2026, 11, 2)


def test_roundtrip_persists_everything(tmp_path: Path) -> None:
    st = RiskStore(tmp_path / "r.db")
    s = st.load_or_init(LIM, "paper", DAY)
    s = fill_buy(reserve(s, "A", D("4.6")), "A", D("4.40"), D("0.10"), D("0.10"))
    s = reserve(s, "B", D("1.5"))
    st.save(s, "paper")
    st.close()
    s2 = RiskStore(tmp_path / "r.db").load_or_init(LIM, "paper", DAY)
    assert s2 == s
    assert s2.open_exposure_usd == D("6.10")


def test_lock_survives_restart_and_requires_manual_unlock(tmp_path: Path) -> None:
    st = RiskStore(tmp_path / "r.db")
    s = st.load_or_init(LIM, "paper", DAY)
    s = fill_buy(reserve(s, "A", D("21")), "A", D("20"), D("0"), D("0"))
    s = close_position(s, "A", D("0"), D("0"), LIM.maximum_cumulative_loss_usd)
    assert s.locked
    st.save(s, "paper")
    st.close()
    st2 = RiskStore(tmp_path / "r.db")
    assert st2.load_or_init(LIM, "paper", DAY).locked
    with pytest.raises(UnlockRefused):
        st2.admin_unlock("paper", "oui", "note")
    with pytest.raises(UnlockRefused):
        st2.admin_unlock("paper", UNLOCK_PHRASE, "  ")
    s3 = st2.admin_unlock("paper", UNLOCK_PHRASE, "analyse post-mortem faite")
    assert not s3.locked and s3.cumulative_loss_usd == D("20")  # les pertes ne sont pas effacées
    assert any(k == "unlock" for _, k, _ in st2.events())


def test_changed_limits_refuse_live_and_reset_paper(tmp_path: Path) -> None:
    stricter = Limits.model_validate({**LIM.model_dump(), "max_trades_per_day": 5})
    live = RiskStore(tmp_path / "live.db")
    live.load_or_init(LIM, "live", DAY)
    with pytest.raises(LimitsChangedError):
        live.load_or_init(stricter, "live", DAY)
    paper = RiskStore(tmp_path / "paper.db")
    paper.load_or_init(LIM, "paper", DAY)
    s = paper.load_or_init(stricter, "paper", DAY)
    assert s.limits_fingerprint == stricter.fingerprint()
    assert any(k == "limits_changed" for _, k, _ in paper.events())


def test_mode_mismatch_refused(tmp_path: Path) -> None:
    st = RiskStore(tmp_path / "r.db")
    st.load_or_init(LIM, "paper", DAY)
    with pytest.raises(LimitsChangedError):
        st.load_or_init(LIM, "live", DAY)


def test_paper_limits_change_keeps_losses_and_positions(tmp_path: Path) -> None:
    # D-032 : passer à 100 trades/jour ne doit ni effacer les pertes consommées, ni perdre les positions ouvertes.
    st = RiskStore(tmp_path / "r.db")
    day = date(2026, 11, 1)
    s = st.load_or_init(LIM, "paper", day)
    s = fill_buy(reserve(s, "A", D("3")), "A", D("2.50"), D("0.08"), D("0.08"))
    s = close_position(s, "A", D("1.00"), D("0.06"), LIM.maximum_cumulative_loss_usd)
    s = fill_buy(reserve(s, "B", D("3")), "B", D("2.50"), D("0.08"), D("0.08"))
    st.save(s, "paper")
    more = LIM.model_copy(update={"max_trades_per_day": 100})
    s2 = st.load_or_init(more, "paper", day)
    assert s2.limits_fingerprint == more.fingerprint() != LIM.fingerprint()
    assert s2.cumulative_loss_usd == s.cumulative_loss_usd > 0 and s2.trades_today == 2
    assert [p.token for p in s2.positions] == ["B"]
    assert any(k == "limits_changed" and "conservé" in d for _, k, d in st.events())
