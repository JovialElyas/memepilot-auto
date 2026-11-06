import json
import sqlite3
import time
import urllib.request
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest

from memepilot.config.envfile import load_project_env
from memepilot.config.limits import load_limits
from memepilot.engine.paper import PaperEngine
from memepilot.gmgn.read_client import ArgValue, GmgnResult
from memepilot.ledger.ledger import Ledger
from memepilot.paper.broker import PaperBroker, PaperRejected
from memepilot.paper.costs import CostModel
from memepilot.paper.exits import ExitConfig, PositionView, check_exit
from memepilot.reporting.telegram import NullNotifier, TelegramNotifier
from memepilot.reporting.weekly import build_weekly, render_markdown
from memepilot.risk.state import close_position as risk_close
from tests.unit.test_hybrid_cycle import FakeClient

ROOT = Path(__file__).resolve().parents[2]
LIM = load_limits(ROOT / "tests" / "fixtures" / "limits_test.yaml")
A = "66auXVTRw9ZekMeB8h5jdJS9WhMRVz8zfMyzqeinpump"
COSTS = CostModel()


# ── coûts et courtier ─────────────────────────────────────────────────────
def test_guardian_estimate_covers_real_round_trip() -> None:
    est = COSTS.guardian_fee_estimate(LIM.max_position_usd)
    worst_real = COSTS.fee(LIM.max_position_usd) * 2  # entrée + sortie à perte (produit ≤ coût)
    assert est >= worst_real and est >= D("0.40")


def test_slippage_model() -> None:
    assert COSTS.slippage(D("5"), D("50000")) == D("0.005") + D("2") * D("5") / D("50000")
    assert COSTS.slippage(D("5"), None) is None
    assert COSTS.slippage(D("5"), D("20")) is None  # glissement > 30 % → ordre impossible


def test_broker_buy_and_sell() -> None:
    b = PaperBroker()
    f = b.buy(A, D("4.60"), D("0.001"), D("80000"))
    assert f.exec_price_usd > f.mid_price_usd and f.qty > 0 and f.fee_usd == COSTS.fee(D("4.60"))
    s = b.sell(A, f.qty, D("0.002"), D("80000"))
    assert s.exec_price_usd < s.mid_price_usd and s.gross_usd > f.gross_usd
    rug = b.sell(A, f.qty, None, None)
    assert rug.gross_usd == 0 and rug.fee_usd == COSTS.network_fee_usd
    with pytest.raises(PaperRejected):
        b.buy(A, D("4.60"), None, D("80000"))


# ── règles de sortie ──────────────────────────────────────────────────────
NOW = 1_800_000_000.0
NO_TP = ExitConfig(take_profit=None)  # mécanismes de secours (trailing, plancher, moitié) testés sans D-035


def pv(**over: Any) -> PositionView:
    base: dict[str, Any] = dict(token=A, entry_price=D("1"), entry_liquidity=D("80000"), peak_price=D("1"),
                                opened_ts=NOW - 300, missing_cycles=0)
    base.update(over)
    return PositionView(**base)


@pytest.mark.parametrize(
    ("view", "price", "liq", "now", "reason"),
    [
        ({}, D("0.74"), D("80000"), NOW, "stop-loss"),
        ({"peak_price": D("1.6")}, D("1.25"), D("80000"), NOW, "trailing"),
        ({}, D("1.02"), D("80000"), NOW + 1800, "time-stop"),
        ({}, D("1.05"), D("40000"), NOW, "liquidité"),
        ({"peak_price": D("1.6")}, D("1.5"), D("80000"), NOW + 15000, "durée maximale"),
        ({"missing_cycles": 4}, None, None, NOW, "prix indisponible"),
    ],
)
def test_exit_rules(view: dict[str, Any], price: D | None, liq: D | None, now: float, reason: str) -> None:
    chk = check_exit(pv(**view), price, liq, now, NO_TP)
    assert chk.reason is not None and reason in chk.reason


def test_hold_and_track_peak() -> None:
    chk = check_exit(pv(), D("1.3"), D("80000"), NOW, NO_TP)
    assert chk.reason is None and chk.peak_price == D("1.3")
    miss = check_exit(pv(), None, None, NOW)
    assert miss.reason is None and miss.missing_cycles == 1
    # gain armé (≥ +50 %) : pas de time-stop même après 30 min
    assert check_exit(pv(peak_price=D("1.55")), D("1.5"), D("80000"), NOW + 2000, NO_TP).reason is None


# ── ledger ────────────────────────────────────────────────────────────────
def test_ledger_idempotency_and_single_open_position(tmp_path: Path) -> None:
    lg = Ledger(tmp_path / "l.db")
    lg.create_order("buy:A:1", 1.0, A, "buy", D("4.6"), None)
    with pytest.raises(sqlite3.IntegrityError):
        lg.create_order("buy:A:1", 2.0, A, "buy", D("4.6"), None)
    f = PaperBroker().buy(A, D("4.6"), D("1"), D("80000"))
    lg.open_position(1.0, A, "T", "momentum", f, D("80000"), None)
    with pytest.raises(sqlite3.IntegrityError):
        lg.open_position(2.0, A, "T", "momentum", f, D("80000"), None)
    assert len(lg.open_positions()) == 1


# ── moteur papier complet ─────────────────────────────────────────────────
class PricedClient(FakeClient):
    """FakeClient (propose A) + prix scriptés pour A via `token info`."""

    def __init__(self, prices: list[str | None]) -> None:
        super().__init__()
        self.prices = prices
        self.cycle = 0  # le prix de A dépend du cycle, pas du nombre d'appels

    async def call(self, command: tuple[str, ...], cache_ttl_s: float | None = None,
                   **options: ArgValue) -> GmgnResult:
        if command == ("token", "info") and options.get("address") == A:
            self.calls += 1
            p = self.prices[min(self.cycle, len(self.prices) - 1)]
            data = {"address": A, "symbol": "GOOD", "decimals": 6, "liquidity": "80000", "price": {"price": p}}
            if p is None:
                data = {"address": A, "symbol": "", "decimals": 0}
            return GmgnResult(command, data, 1.0, "", time.time())
        return await super().call(command, cache_ttl_s, **options)


async def test_full_paper_round_trip_stop_loss(tmp_path: Path) -> None:
    notes = NullNotifier()
    # appel 1 : achat à 1.0 ; appel 2 (cycle 2, surveillance) : 0.70 → stop-loss
    client = PricedClient(["1.0", "0.70"])
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM, notifier=notes)
    r1 = await eng.run_cycle()
    client.cycle = 1
    assert r1.buys == ["GOOD"] and r1.state and len(r1.state.positions) == 1
    assert r1.verdicts[A].approved and r1.verdicts[A].size_usd == D("4.60")
    assert not any(c[0] in ("swap", "order", "multi-swap", "cooking") for c in client.seen)
    r2 = await eng.run_cycle()
    assert r2.sells and "stop-loss" in r2.sells[0][1] and r2.sells[0][2] < 0
    s = r2.state
    assert s is not None and s.daily_loss_usd == -r2.sells[0][2]
    # Le token est re-proposé au cycle 2, mais D-027 impose 2 h de pause après une perte sur ce token
    assert not r2.buys and not r2.verdicts[A].approved and "pause" in r2.verdicts[A].reasons[0]
    assert s.daily_loss_usd + s.open_exposure_usd <= LIM.daily_loss_limit_usd
    assert eng.ledger.closed_trades()[0].pnl_usd == -s.daily_loss_usd
    assert eng.cash(s) == eng.ledger_cash()  # ledger et état de risque concordent
    assert any("Achat papier" in m for m in notes.sent) and any("Vente papier" in m for m in notes.sent)
    eng.close()


async def test_restart_resumes_and_mismatch_halts(tmp_path: Path) -> None:
    db = str(tmp_path / "p.db")
    eng = PaperEngine(PricedClient(["1.0"]), db, LIM)
    await eng.run_cycle()
    eng.close()
    # Redémarrage : nouvelle instance, même base → la position est reprise, pas de HALT
    eng2 = PaperEngine(PricedClient(["1.05"]), db, LIM)
    r = await eng2.run_cycle()
    assert r.state and len(r.state.positions) == 1 and not r.state.halted
    # Corruption simulée : la position disparaît du ledger mais pas de l'état → HALT
    eng2.ledger._x("UPDATE positions SET status='closed', pnl_usd='0', proceeds_usd='0', entry_fee_usd='0', "
                   "exit_fee_usd='0', closed_ts=1, exit_reason='test'")
    notes = NullNotifier()
    eng2.notifier = notes
    r2 = await eng2.run_cycle()
    assert r2.state and r2.state.halted and not r2.buys
    assert any("HALT" in m for m in notes.sent)
    eng2.close()


async def test_interrupted_reservation_is_cancelled(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from memepilot.risk.state import reserve
    from memepilot.risk.store import RiskStore

    db = str(tmp_path / "p.db")
    today = datetime.now(UTC).date()
    st = RiskStore(db)
    s = reserve(st.load_or_init(LIM, "paper", today), A, D("5"))
    st.save(s, "paper")
    lg = Ledger(db)
    lg.create_order("buy:A:crash", time.time(), A, "buy", D("4.6"), None)
    eng = PaperEngine(PricedClient(["1.0"]), db, LIM)
    r = await eng.run_cycle()
    assert r.state and not r.state.halted
    assert eng.ledger.failed_orders() and eng.ledger.events(kinds=("reconcile",))
    eng.close()


# ── rapport hebdomadaire ──────────────────────────────────────────────────
def test_weekly_report_from_ledger(tmp_path: Path) -> None:
    lg = Ledger(tmp_path / "w.db")
    b = PaperBroker()
    t = 1_000_000.0
    for i, (tok, exit_px, strat) in enumerate([("A1", "1.6", "momentum"), ("A2", "0.7", "smart_money"),
                                                ("A3", "0.0", "momentum")]):
        f = b.buy(tok, D("4.6"), D("1"), D("80000"))
        lg.open_position(t + i, tok, tok, strat, f, D("80000"), None)
        sell = b.sell(tok, f.qty, D(exit_px) if exit_px != "0.0" else None, D("80000"))
        lg.close_position(t + i + 100, tok, sell, sell.gross_usd - f.gross_usd - f.fee_usd - sell.fee_usd, "test")
        lg.record_fill(0, t + i, f)
        lg.record_fill(0, t + i + 100, sell)
    for k, eq in enumerate(["50", "53", "49", "45", "47"]):
        lg.snapshot_equity(t + k * 10, D(eq), D(0), D(0))
    r = build_weekly(lg, t - 1, t + 1000, D("50"))
    assert r.trades == 3 and r.wins == 1
    assert r.largest_loss < -4 and r.largest_win > 0
    assert r.max_drawdown_usd == D("8.00")
    assert set(r.by_strategy) == {"momentum", "smart_money"}
    assert r.fees_usd > 0 and r.slippage_usd > 0
    assert "INSUFFISANT" in r.verdict()
    md = render_markdown(r)
    assert "Drawdown max" in md and "momentum" in md


# ── Telegram et .env ──────────────────────────────────────────────────────
async def test_telegram_payload_and_failure_isolated() -> None:
    sent: list[dict[str, Any]] = []

    def ok(req: urllib.request.Request, timeout: float) -> bytes:
        assert req.full_url.startswith("https://api.telegram.org/bot123:ABC/")
        sent.append(json.loads(req.data or b"{}"))  # type: ignore[arg-type]
        return b'{"ok": true}'

    n = TelegramNotifier("123:ABC", "42", opener=ok, min_interval_s=0)
    await n.send("bonjour")
    assert sent[0]["chat_id"] == "42" and sent[0]["text"] == "bonjour"

    def boom(req: urllib.request.Request, timeout: float) -> bytes:
        raise OSError("réseau coupé pour bot123:ABC")

    bad = TelegramNotifier("123:ABC", "42", opener=boom, min_interval_s=0)
    await bad.send("x")  # ne lève pas
    assert bad.failures == 1


def test_envfile_ignores_gmgn_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(__import__("os").environ):
        if k.startswith(("TELEGRAM_", "MEMEPILOT_")):
            monkeypatch.delenv(k)
    p = tmp_path / ".env"
    p.write_text('GMGN_API_KEY=secret\nTELEGRAM_CHAT_ID="42"\n# commentaire\nMEMEPILOT_MODE=paper\n', encoding="utf-8")
    env = load_project_env(p)
    assert env == {"TELEGRAM_CHAT_ID": "42", "MEMEPILOT_MODE": "paper"}


def test_risk_close_lock_matches_ledger_amounts() -> None:
    from datetime import date

    from memepilot.risk.state import fill_buy, new_state, reserve

    s = new_state(D("50"), date(2026, 11, 2), LIM.fingerprint())
    s = fill_buy(reserve(s, A, D("5")), A, D("4.6"), D("0.10"), D("0.30"))
    s = risk_close(s, A, D("0"), D("0.05"), LIM.maximum_cumulative_loss_usd)
    assert s.realized_equity_usd == D("50") - D("4.6") - D("0.10") - D("0.05")


async def test_manual_halt_during_cycle_is_not_overwritten(tmp_path: Path) -> None:
    from dataclasses import replace
    from datetime import UTC, datetime

    from memepilot.risk.state import set_halt
    from memepilot.risk.store import RiskStore

    db = str(tmp_path / "p.db")
    eng = PaperEngine(PricedClient(["1.0"]), db, LIM)
    await eng.run_cycle()
    # risk_admin.py halt, exécuté depuis un autre processus pendant que le moteur tourne
    admin = RiskStore(db)
    s = admin.load_or_init(LIM, "paper", datetime.now(UTC).date())
    admin.save(set_halt(s, True, "manuel"), "paper", event=("halt", "manuel"))
    eng._save(replace(s, halted=False))  # le moteur termine son cycle avec un état antérieur au HALT
    assert eng.store.load_or_init(LIM, "paper", s.day).halted
    r = await eng.run_cycle()
    assert r.state and r.state.halted and not r.buys
    eng.close()


# ── boucle rapide (surveillance des positions toutes les quelques secondes) ──
async def test_fast_monitor_sells_without_a_full_cycle(tmp_path: Path) -> None:
    client = PricedClient(["1.0", "0.70"])
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM)
    await eng.run_cycle()
    client.cycle = 1
    seen_before = len(client.seen)
    r = await eng.monitor(interval_s=5)
    assert r.sells and "stop-loss" in r.sells[0][1]
    assert r.state and not r.state.positions and eng.cash(r.state) == eng.ledger_cash()
    # aucun scan : la boucle rapide n'appelle que `token info` (prix de A, poids 1)
    assert not any(c[0] == "market" for c, _ in client.seen[seen_before:])
    eng.close()


async def test_fast_monitor_noop_without_positions(tmp_path: Path) -> None:
    client = PricedClient(["1.0"])
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM)
    r = await eng.monitor(interval_s=5)
    assert client.calls == 0 and not r.sells
    eng.close()


async def test_fast_monitor_missing_price_threshold_is_time_based(tmp_path: Path) -> None:
    client = PricedClient(["1.0", None])
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM)
    await eng.run_cycle()
    client.cycle = 1
    for _ in range(10):  # 10 × 5 s = 50 s sans prix : pas encore de vente forcée à 0
        r = await eng.monitor(interval_s=5)
        assert not r.sells
    eng.close()


class FrozenClient(PricedClient):
    def __init__(self) -> None:
        super().__init__(["1.0"])
        self.frozen = False

    async def call(self, command: tuple[str, ...], cache_ttl_s: float | None = None,
                   **options: ArgValue) -> GmgnResult:
        if self.frozen:
            from memepilot.gmgn.read_client import GmgnRateLimited
            raise GmgnRateLimited("RATE_LIMIT_BANNED — gel 60s", 60)
        return await super().call(command, cache_ttl_s, **options)


async def test_rate_limit_is_not_a_missing_price(tmp_path: Path) -> None:
    client = FrozenClient()
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM)
    await eng.run_cycle()
    client.frozen = True
    for _ in range(100):
        r = await eng.monitor(interval_s=5)
        assert not r.sells and r.errors
    assert eng.ledger.open_positions()[0].missing_cycles == 0
    eng.close()


class SeqClient(PricedClient):
    """Prix de A appel par appel (file `queue`), puis prix du cycle quand la file est vide."""

    def __init__(self, prices: list[str | None]) -> None:
        super().__init__(prices)
        self.queue: list[str] = []

    async def call(self, command: tuple[str, ...], cache_ttl_s: float | None = None,
                   **options: ArgValue) -> GmgnResult:
        if command == ("token", "info") and options.get("address") == A and self.queue:
            p = self.queue.pop(0)
            data = {"address": A, "symbol": "GOOD", "decimals": 6, "liquidity": "80000", "price": {"price": p}}
            return GmgnResult(command, data, 1.0, "", time.time())
        return await super().call(command, cache_ttl_s, **options)


async def test_price_crash_is_rechecked_within_the_same_control(tmp_path: Path) -> None:
    # D-030 : une chute > 50 % est revérifiée tout de suite (≈ 1 s) au lieu d'attendre le contrôle suivant.
    client = SeqClient(["1.0"])
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM)
    await eng.run_cycle()
    client.queue = ["0.0001", "1.02"]  # prix aberrant isolé, démenti à la revérification : on garde
    r = await eng.monitor(2)
    assert not r.sells and any("aberrant ignoré" in e for e in r.errors)
    assert r.state and len(r.state.positions) == 1
    client.queue = ["0.0001", "0.0001", "0.0001"]  # vrai krach, confirmé dans le même contrôle : on sort
    r = await eng.monitor(2)
    assert r.sells and "stop-loss" in r.sells[0][1]
    eng.close()


async def test_price_crash_without_recheck_waits_next_control(tmp_path: Path) -> None:
    # Revérification impossible (désactivée ici) : comportement d'avant, confirmation au contrôle suivant.
    client = PricedClient(["1.0", "0.0001", "1.02", "0.0001", "0.0001"])
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM)
    eng.crash_recheck_s = None
    await eng.run_cycle()
    client.cycle = 1
    r = await eng.monitor(5)
    assert not r.sells and any("prix suspect" in e for e in r.errors)
    client.cycle = 2  # le prix revient : c'était un glitch
    r = await eng.monitor(5)
    assert not r.sells and r.state and len(r.state.positions) == 1
    client.cycle = 3
    assert not (await eng.monitor(5)).sells  # nouvelle chute : d'abord suspecte…
    client.cycle = 4
    r = await eng.monitor(5)
    assert r.sells and "stop-loss" in r.sells[0][1]  # …puis confirmée : on sort
    eng.close()


# ── D-026 : sorties plus serrées, veto « achat de sommet », prix enregistrés et rejeu ──
def test_new_trailing_locks_gain_like_niketyson() -> None:
    # Cas réel : entrée 1.0, sommet +33 %, puis rechute. Nouvelle règle : vente dès −10 % sous le sommet.
    chk = check_exit(pv(peak_price=D("1.33")), D("1.19"), D("80000"), NOW, NO_TP)
    assert chk.reason is not None and "trailing" in chk.reason
    # Ancienne règle (+50 % / −20 %) : aucune sortie, le gain est rendu
    old = ExitConfig(trail_activation=D("0.50"), trail_drawdown=D("0.20"), take_profit=None)
    assert check_exit(pv(peak_price=D("1.33")), D("1.19"), D("80000"), NOW, old).reason is None
    # Sommet sous +15 % : trailing pas encore armé
    assert check_exit(pv(peak_price=D("1.12")), D("1.00"), D("80000"), NOW, NO_TP).reason is None


def test_chase_veto_without_candles() -> None:
    from memepilot.gmgn.models import TrendingRow
    from memepilot.scanner.prefilter import Candidate
    from memepilot.signals.momentum import momentum_signal
    row = {"address": A, "price_change_percent5m": 23.1, "price_change_percent1h": 175.0, "buys": 70, "sells": 30}
    rep = momentum_signal(Candidate.from_trending(TrendingRow.model_validate(row)))
    assert any("achat de sommet" in v for v in rep.vetoes)
    calm = {**row, "price_change_percent5m": 8.0, "price_change_percent1h": 60.0}
    assert not momentum_signal(Candidate.from_trending(TrendingRow.model_validate(calm))).vetoes


async def test_ticks_recorded_and_replay_compares_rules(tmp_path: Path) -> None:
    from memepilot.paper.replay import default_grid, replay_position
    # Trajectoire type niketyson : achat 1.0, sommet +33 %, rechute progressive jusqu'à −2 %
    path = ["1.0", "1.33", "1.25", "1.18", "1.05", "0.98"]
    client = PricedClient(path)
    clock = {"t": 1_800_000_000.0}
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM, exits=NO_TP, clock=lambda: clock["t"])
    await eng.run_cycle()
    for cyc in range(1, len(path)):
        client.cycle = cyc
        clock["t"] += 5
        await eng.monitor(5)
    closed = eng.ledger.closed_for_replay()
    assert closed and "trailing" in closed[0]["exit_reason"]  # vendu à 1.18 (−11 % sous le sommet)
    ticks = eng.ledger.ticks(closed[0]["token"], closed[0]["opened_ts"])
    # le prix continue d'être enregistré APRÈS la vente (suivi de 30 min) : 1.05 puis 0.98
    assert [t[1] for t in ticks] == [D(p) for p in path[1:]]
    results = {label: replay_position(closed[0], ticks, cfg) for label, cfg in default_grid()}
    new, old = results["avant D-035 (sans prise de profit totale)"], results["ancienne (+50 % / −20 %)"]
    assert new is not None and old is not None
    assert new.pnl_usd > 0 > old.pnl_usd  # l'ancienne règle aurait tout rendu (et même perdu, frais compris)
    eng.close()



# ── D-027 : pause après une perte, positions supplémentaires seulement sous contrôle ──
async def test_rebuy_allowed_again_after_cooldown(tmp_path: Path) -> None:
    clock = {"t": 1_800_000_000.0}
    client = PricedClient(["1.0", "0.70", "1.0"])
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM, clock=lambda: clock["t"])
    await eng.run_cycle()
    client.cycle = 1
    clock["t"] += 60
    r = await eng.run_cycle()  # stop-loss, puis refus du rachat
    assert r.sells and not r.buys
    client.cycle = 2
    clock["t"] += 7200 + 60  # 2 h plus tard : le Guardian refait son évaluation normale
    r = await eng.run_cycle()
    assert r.buys == ["GOOD"]
    eng.close()


def test_control_gate_blocks_extra_position_until_prices_confirmed(tmp_path: Path) -> None:
    eng = PaperEngine(PricedClient(["1.0"]), str(tmp_path / "p.db"), LIM)
    now = 1_800_000_000.0
    f = PaperBroker().buy(A, D("2.5"), D("1"), D("80000"))
    eng.ledger.open_position(now, A, "GOOD", "momentum", f, D("80000"), None)
    other = "So11111111111111111111111111111111111111112"
    assert eng._pre_buy_block(other, now) is not None  # prix de GOOD jamais confirmé
    eng.last_prices[A], eng._last_tick_ts[A] = f.exec_price_usd, now
    assert eng._pre_buy_block(other, now + 5) is None  # sous contrôle : 2e position possible
    assert eng._pre_buy_block(other, now + 60) is not None  # prix trop ancien (> 20 s)
    eng.last_prices[A], eng._last_tick_ts[A] = f.exec_price_usd * D("0.85"), now + 60
    assert "perd" in (eng._pre_buy_block(other, now + 61) or "")  # position en difficulté (−15 %)
    eng.close()


# ── D-029 : ne plus acheter en plein pic (rejeu des 10 premiers achats réels du 2026-11-01) ──
REAL_ENTRIES = [  # (symbole, variation 5 min, variation 1 h, accélération bougies, plus haut atteint après achat)
    ("BLUE", 0.06, 0.03, 0.175, 0.03), ("fone", 0.15, 0.24, 0.146, 0.05), ("SIS", 0.15, 0.00, 0.226, 0.03),
    ("Anakin", 0.11, 0.23, 0.216, 0.00), ("PAWCAT", 0.23, 0.66, -0.085, 0.01), ("白いヤク", 0.08, 1.42, -0.055, 0.00),
    ("tipr", 0.07, 0.04, 0.034, 0.23), ("KIM", 0.07, 0.17, 0.048, 0.15), ("AODF", 0.00, 0.41, -0.017, 0.50),
]


def _candles_with_accel(accel: float) -> list[Any]:
    """15 bougies 1 min dont les 5 dernières montent de `accel` (le début reste plat)."""
    from memepilot.gmgn.models import Candle
    closes = [1.0] * 10 + [1.0 * (1 + accel) ** ((i + 1) / 5) for i in range(5)]
    return [Candle(time=1_800_000_000 + 60 * i, open=c, high=c, low=c, close=c) for i, c in enumerate(closes)]


@pytest.mark.parametrize(("sym", "c5", "c1h", "accel", "peak"), REAL_ENTRIES)
def test_spike_entries_are_now_refused(sym: str, c5: float, c1h: float, accel: float, peak: float) -> None:
    from memepilot.gmgn.models import TrendingRow
    from memepilot.scanner.prefilter import Candidate
    from memepilot.signals.momentum import momentum_signal
    row = {"address": A, "price_change_percent5m": c5 * 100, "price_change_percent1h": c1h * 100,
           "buys": 70, "sells": 30}
    rep = momentum_signal(Candidate.from_trending(TrendingRow.model_validate(row)), _candles_with_accel(accel))
    refused = bool(rep.vetoes)
    # Les 6 achats achetés en plein pic (aucun n'a dépassé +5 %) sont refusés ; les 3 qui sont montés passent
    assert refused == (peak < 0.10), (sym, rep.vetoes)


# ── D-030 : plancher de gain, moitié à +50 %, trailing serré, achat confirmé, délai de vente ──
def test_profit_floor_keeps_a_winner_from_ending_negative() -> None:
    # Sommet +17 % : l'ancien trailing (−10 %) vendait à +5 %, soit une perte une fois les frais payés.
    chk = check_exit(pv(peak_price=D("1.17")), D("1.07"), D("80000"), NOW, NO_TP)
    assert chk.reason is not None and "plancher de gain" in chk.reason
    assert check_exit(pv(peak_price=D("1.17")), D("1.09"), D("80000"), NOW, NO_TP).reason is None
    off = ExitConfig(profit_floor=None, take_profit=None)
    assert check_exit(pv(peak_price=D("1.17")), D("1.07"), D("80000"), NOW, off).reason is None


def test_tight_trailing_above_40_percent() -> None:
    assert "trailing" in (check_exit(pv(peak_price=D("1.6")), D("1.48"), D("80000"), NOW, NO_TP).reason or "")  # −7,5 %
    assert check_exit(pv(peak_price=D("1.6")), D("1.50"), D("80000"), NOW, NO_TP).reason is None  # −6,25 %
    assert check_exit(pv(peak_price=D("1.3")), D("1.19"), D("80000"), NOW, NO_TP).reason is None  # < +40 % : −10 %


def test_partial_take_profit_signal_once() -> None:
    chk = check_exit(pv(), D("1.55"), D("80000"), NOW, NO_TP, partial_done=False)
    assert chk.partial and chk.reason is None
    assert not check_exit(pv(), D("1.55"), D("80000"), NOW, NO_TP, partial_done=True).partial
    assert not check_exit(pv(), D("1.40"), D("80000"), NOW, NO_TP, partial_done=False).partial


async def test_partial_sell_banks_half_and_keeps_the_rest(tmp_path: Path) -> None:
    client = PricedClient(["1.0", "1.55", "1.45"])
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM, exits=NO_TP)
    r = await eng.run_cycle()
    full = eng.ledger.open_positions()[0]
    client.cycle = 1
    r = await eng.monitor(2)
    assert r.sells and "partielle" in r.sells[0][1] and r.sells[0][2] > 0
    rest = eng.ledger.open_positions()[0]
    assert rest.partial_done and rest.qty == full.qty - (full.qty * D("0.5")).quantize(D("0.000001"))
    assert r.state and r.state.positions[0].cost_usd == full.cost_usd - (full.cost_usd / 2).quantize(D("0.01"))
    assert eng.cash(r.state) == eng.ledger_cash()  # ledger et état de risque concordent
    client.cycle = 2  # 1.45 : au-dessus du trailing serré (1.55 × 0,93 = 1.4415) → on garde, pas de 2e moitié
    r = await eng.monitor(2)
    assert not r.sells and len(eng.ledger.open_positions()) == 1
    assert len(eng.ledger.closed_trades()) == 1
    # Le rejeu refond la partie vendue dans la position : il repart de la position entière
    eng.ledger._x("UPDATE positions SET status='closed', closed_ts=2e9, exit_price='1.4', proceeds_usd='0', "
                  "exit_fee_usd='0', pnl_usd='0', exit_reason='test' WHERE status='open'")
    merged = eng.ledger.closed_for_replay()
    assert len(merged) == 1 and merged[0]["qty"] == full.qty
    eng.close()


async def test_entry_waits_for_a_calm_price(tmp_path: Path) -> None:
    clock = {"t": 1_800_000_000.0}
    client = PricedClient(["1.0", "0.94"])
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM, clock=lambda: clock["t"])
    eng.entry_confirm_s = 5.0
    r = await eng.run_cycle()
    assert not r.buys and r.state and len(r.state.pending) == 1  # réservé, pas encore acheté
    clock["t"] += 2
    r = await eng.monitor(2)
    assert not r.buys  # pas encore 5 s
    clock["t"] += 3
    client.cycle = 1  # −6 % pendant l'attente : l'achat est annulé, la réservation libérée
    r = await eng.monitor(2)
    assert not r.buys and any("chute pendant la confirmation" in e for e in r.errors)
    assert r.state and not r.state.pending and not r.state.positions and r.state.trades_today == 0
    assert eng.ledger.count("orders", "state = 'CANCELLED'") == 1
    eng.close()


@pytest.mark.parametrize(("second", "bought"), [("1.03", True), ("1.10", False)])
async def test_entry_confirmation_buys_if_stable_and_refuses_a_running_spike(tmp_path: Path, second: str,
                                                                            bought: bool) -> None:
    clock = {"t": 1_800_000_000.0}
    client = PricedClient(["1.0", second])
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM, clock=lambda: clock["t"])
    eng.entry_confirm_s = 5.0
    await eng.run_cycle()
    clock["t"] += 6
    client.cycle = 1
    r = await eng.monitor(2)
    assert (r.buys == ["GOOD"]) is bought
    if bought:
        assert eng.ledger.open_positions()[0].entry_price > D("1.03")  # exécuté au prix confirmé (+ glissement)
    else:
        assert any("pic" in e for e in r.errors)
    eng.close()


async def test_paper_sell_uses_the_worse_price_after_latency(tmp_path: Path) -> None:
    client = SeqClient(["1.0"])
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM)
    await eng.run_cycle()
    client.queue = ["0.70", "0.65"]  # déclenchement à 0.70, prix vu ~1 s plus tard : 0.65
    r = await eng.monitor(2)
    assert r.sells and "stop-loss" in r.sells[0][1]
    mid = eng.ledger._x("SELECT mid_price FROM fills WHERE side='sell'").fetchone()[0]
    assert D(mid) == D("0.65")
    eng.close()


def test_replay_compares_d030_with_previous_rules() -> None:
    from memepilot.paper.replay import default_grid, replay_position
    pos = {"token": A, "symbol": "X", "opened_ts": NOW, "entry_price": D("1"), "entry_liquidity": D("80000"),
           "qty": D("2.5"), "cost_usd": D("2.50"), "entry_fee_usd": D("0.08")}
    # Cas AODF : +58 %, puis effondrement brutal à −80 % en un contrôle
    ticks = [(NOW + 5 * i, D(p), D("80000")) for i, p in enumerate(["1.2", "1.58", "1.57", "0.20"])]
    grid = dict(default_grid())
    new = replay_position(pos, ticks, grid["avant D-035 (sans prise de profit totale)"])
    old = replay_position(pos, ticks, grid["avant D-030 (sans plancher, ni moitié à +50 %, ni −7 %)"])
    assert new is not None and old is not None
    # La moitié encaissée à +58 % amortit le krach : perte ≈ −0,50 $ au lieu de ≈ −2,10 $
    assert new.pnl_usd - old.pnl_usd > D("1.5") and "partielle" not in new.reason


# ── D-031 : critère de rentabilité du propriétaire ──
def test_owner_goal_win_on_net_profit_or_75_percent(tmp_path: Path) -> None:
    from memepilot.reporting.weekly import GOAL_TRADES, owner_goal
    lg = Ledger(tmp_path / "g.db")
    b = PaperBroker()

    def trade(i: int, exit_px: str) -> None:
        tok = f"T{i}"
        f = b.buy(tok, D("2.5"), D("1"), D("1000000"))
        lg.open_position(100.0 + i, tok, tok, "momentum", f, D("1000000"), None)
        sell = b.sell(tok, f.qty, D(exit_px), D("1000000"))
        lg.close_position(200.0 + i, tok, sell, sell.gross_usd - f.gross_usd - f.fee_usd - sell.fee_usd, "test")

    trade(0, "1.5")
    g = owner_goal(lg, since_ts=0)
    assert g.status == "EN COURS" and "1/30" in g.line()
    for i in range(1, GOAL_TRADES):  # 1 gros gain + 29 petites pertes : P&L négatif, 3 % gagnants
        trade(i, "0.97")
    assert owner_goal(lg, since_ts=0).status == "PAS ATTEINT"
    assert owner_goal(lg, since_ts=150).trades == 0  # trades antérieurs aux nouvelles règles : exclus
    for i in range(GOAL_TRADES, GOAL_TRADES + 23):  # 23 petits gains : 23/30 = 77 % gagnants
        trade(i, "1.08")
    g = owner_goal(lg, since_ts=0)
    assert g.window == GOAL_TRADES and g.win_rate >= 0.75 and g.status == "ATTEINT"
    lg.close()


# ── D-034 : la surveillance n'est plus bloquée pendant un scan ──
async def test_monitor_runs_while_the_scan_is_analysing(tmp_path: Path) -> None:
    import asyncio

    class SlowScanClient(PricedClient):
        def __init__(self) -> None:
            super().__init__(["1.0", "0.70"])
            self.gate = asyncio.Event()
            self.slow = False

        async def call(self, command: tuple[str, ...], cache_ttl_s: float | None = None,
                       **options: ArgValue) -> GmgnResult:
            if self.slow and command[0] == "market":
                await self.gate.wait()  # analyse « longue » (réseau)
            return await super().call(command, cache_ttl_s, **options)

    client = SlowScanClient()
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM)
    await eng.run_cycle()  # achat à 1.0
    client.slow = True
    scan = asyncio.create_task(eng.run_cycle())
    await asyncio.sleep(0.05)  # le scan est en pleine analyse…
    client.cycle = 1  # …quand le prix s'effondre
    r = await asyncio.wait_for(eng.monitor(2), timeout=2)  # …et la surveillance vend quand même
    assert r.sells and "stop-loss" in r.sells[0][1]
    client.gate.set()
    r2 = await scan
    assert r2.state is not None and not r2.state.positions and eng.cash(r2.state) == eng.ledger_cash()
    eng.close()



# ── D-035 : prise de profit totale à +12 % (prendre et partir) ──
def test_take_profit_sells_everything_at_12_percent() -> None:
    chk = check_exit(pv(), D("1.13"), D("80000"), NOW, partial_done=False)
    assert chk.reason is not None and "prise de profit" in chk.reason and not chk.partial
    assert check_exit(pv(), D("1.11"), D("80000"), NOW).reason is None
    # un saut direct à +60 % : on vend TOUT (pas seulement la moitié)
    assert "prise de profit" in (check_exit(pv(), D("1.6"), D("80000"), NOW, partial_done=False).reason or "")
    # le stop-loss reste prioritaire
    assert "stop-loss" in (check_exit(pv(), D("0.7"), D("80000"), NOW).reason or "")


def test_replay_take_profit_beats_holding_on_aodf_like_path() -> None:
    from memepilot.paper.replay import default_grid, replay_position
    pos = {"token": A, "symbol": "X", "opened_ts": NOW, "entry_price": D("1"), "entry_liquidity": D("80000"),
           "qty": D("5"), "cost_usd": D("5.00"), "entry_fee_usd": D("0.10")}
    ticks = [(NOW + 5 * i, D(p), D("80000")) for i, p in enumerate(["1.05", "1.14", "1.58", "1.57", "0.08"])]
    grid = dict(default_grid())
    now_ = replay_position(pos, ticks, grid["D-035 (prise de profit +12 %, stop −25 %, 30 min)"])
    before = replay_position(pos, ticks, grid["avant D-035 (sans prise de profit totale)"])
    assert now_ is not None and before is not None and now_.pnl_usd > 0 > before.pnl_usd


# ── D-037 : suivi des tokens refusés ──
async def test_refused_proposal_is_tracked_and_judged(tmp_path: Path) -> None:
    from memepilot.paper.shadow import evaluate_shadows, render_shadows
    clock = {"t": 1_800_000_000.0}
    client = PricedClient(["1.0", "0.70", "0.60"])
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM, clock=lambda: clock["t"])
    await eng.run_cycle()  # achat
    client.cycle = 1
    clock["t"] += 60
    r = await eng.run_cycle()  # stop-loss, puis refus du rachat (pause de 2 h) → suivi
    assert r.sells and not r.buys
    sh = eng.ledger.shadows()
    assert sh and sh[0]["kind"] == "refus contrôle" and "pause" in sh[0]["reason"]
    client.cycle = 2
    for _ in range(3):  # relevés toutes les 15 s (le 1er sert de prix de référence si le scan n'en donnait pas)
        clock["t"] += 16
        await eng.monitor(2)
    assert len(eng.ledger.shadow_ticks(sh[0]["id"])) == 3
    # Le scan de test ne donne pas de prix : le 1er relevé sert de référence (0.70), les suivants à 0.60
    eng.ledger._x("UPDATE shadow_ticks SET price = '0.70' WHERE rowid = (SELECT MIN(rowid) FROM shadow_ticks)")
    stats = evaluate_shadows(eng.ledger)
    assert stats and stats[0].count == 1 and stats[0].would_pnl < 0 and "UTILE" in stats[0].verdict
    assert "refus contrôle" in render_shadows(stats)
    clock["t"] += 1900  # suivi terminé après 30 min
    await eng.monitor(2)
    assert not eng._shadow
    eng.close()



def test_full_slots_refusals_are_tracked_separately(tmp_path: Path) -> None:
    from memepilot.risk.guardian import RiskDecision
    eng = PaperEngine(PricedClient(["1.0"]), str(tmp_path / "p.db"), LIM)
    v = RiskDecision(False, D(0), ("2 position(s)/ordre(s) ≥ max 2",), D(5), D(5))
    first = v.reasons[0]
    kind = "places pleines" if "position(s)/ordre(s)" in first else "refus Guardian"
    eng._shadow_add(A, "GOOD", kind, first, D("1"), D("80000"), 60.0, NOW)
    assert eng.ledger.shadows()[0]["kind"] == "places pleines"
    eng.close()



# ── D-040 : stratégie « 5 minutes » ──
def test_five_minute_strategy() -> None:
    from memepilot.paper.exits import CURRENT_EXITS as CUR
    assert "prise de profit" in (check_exit(pv(), D("1.09"), D("80000"), NOW, CUR).reason or "")  # +9 % ≥ +8 %
    assert "stop-loss" in (check_exit(pv(), D("0.84"), D("80000"), NOW, CUR).reason or "")  # −16 %
    young = pv(opened_ts=NOW - 200)
    assert check_exit(young, D("1.03"), D("80000"), NOW, CUR).reason is None  # 3 min 20, +3 % : on garde
    old = pv(opened_ts=NOW - 301)
    assert check_exit(old, D("1.03"), D("80000"), NOW, CUR).reason is not None  # 5 min passées : on sort


def test_shadow_missing_prices_are_ignored_not_zero(tmp_path: Path) -> None:
    from memepilot.paper.shadow import evaluate_shadows
    lg = Ledger(tmp_path / "s.db")
    sid = lg.add_shadow(A, "X", "sous le seuil", "score 54", NOW, D("1"), D("80000"), 54.0)
    for i in range(8):  # relais muet pendant 8 relevés : ce n'est PAS une chute à zéro
        lg.record_shadow_tick(sid, NOW + 15 * (i + 1), None, None)
    lg.record_shadow_tick(sid, NOW + 200, D("1.10"), D("80000"))
    st = evaluate_shadows(lg)
    assert st and st[0].would_pnl > 0
    lg.close()


# ── D-041 : laboratoire (observation sans achat) ──
async def test_observe_mode_never_buys_and_tracks_every_candidate(tmp_path: Path) -> None:
    clock = {"t": 1_800_000_000.0}
    client = PricedClient(["1.0"])
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM, clock=lambda: clock["t"])
    eng.observe_only = True
    r = await eng.run_cycle()
    assert not r.buys and not eng.ledger.open_positions() and not eng.ledger.count("orders")
    lab = eng.ledger.shadows(kind="labo")
    assert lab and lab[0]["features"]["signals"] and "decision" in lab[0]["features"]
    for _ in range(3):  # prix toutes les 5 s (client de test sans lots : relevés un par un)
        clock["t"] += 5
        await eng.monitor(2)
    assert eng.ledger.shadow_ticks(lab[0]["id"])
    eng2 = PaperEngine(client, str(tmp_path / "p.db"), LIM, clock=lambda: clock["t"])
    assert eng2._lab  # redémarrage : le suivi en cours (fenêtre de 4 h) reprend
    eng2.close()
    clock["t"] += 4 * 3600 + 10  # suivi terminé après 4 h
    await eng.monitor(2)
    assert not eng._lab
    eng.close()


def test_lab_hypotheses_need_both_halves(tmp_path: Path) -> None:
    from memepilot.paper.lab import evaluate_lab, render_lab
    lg = Ledger(tmp_path / "l.db")
    for i in range(140):  # 140 candidats ; momentum ≥ 80 → +10 % en 30 s, les autres → −20 %
        strong = i % 2 == 0
        f = {"decision": "NO_TRADE", "signals": {"momentum": {"score": 90 if strong else 40, "confidence": 1.0,
                                                              "vetoes": []}}, "candidate": {"liquidity": 30000}}
        sid = lg.add_shadow(f"T{i}", f"T{i}", "labo", "", NOW + 100 * i, None, None, 50.0, f)
        lg.record_shadow_tick(sid, NOW + 100 * i + 5, D("1"), D("80000"))
        lg.record_shadow_tick(sid, NOW + 100 * i + 35, D("1.10") if strong else D("0.80"), D("80000"))
        lg.record_shadow_tick(sid, NOW + 100 * i + 400, D("1"), D("80000"))  # suivi couvrant les 5 min
    total, res = evaluate_lab(lg)
    by = {r.name: r for r in res}
    assert total == 140 and "VALIDÉE" in by["H1 : momentum ≥ 80 (sortie 5 min)"].verdict
    assert "REJETÉE" in by["Tous les candidats (repère, sortie 5 min)"].verdict
    assert "H1 : momentum ≥ 80" in render_lab(total, res)
    from memepilot.paper.shadow import evaluate_shadows
    assert evaluate_shadows(lg) == []  # le laboratoire n'entre pas dans le bilan des refus
    lg.close()


async def test_relay_batch_prices_one_request_per_30_tokens() -> None:
    from memepilot.relay.client import RelayClient
    b58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    addrs = ["So11111111111111111111111111111111111111" + b58[i // 58] + b58[i % 58] for i in range(45)]
    calls: list[str] = []

    def fetch(url: str, timeout: float) -> object:
        calls.append(url)
        toks = url.rsplit("/", 1)[1].split(",")
        return [{"baseToken": {"address": t}, "priceUsd": "0.5", "liquidity": {"usd": 9000}} for t in toks]

    rc = RelayClient(fetch=fetch)
    got = await rc.batch_prices(addrs)
    assert len(calls) == 2 and len(got) == 45 and got[addrs[0]] == (0.5, 9000.0)



def test_lab_drops_isolated_price_glitches() -> None:
    from memepilot.paper.lab import drop_glitches
    path = ["1", "1.01", "40", "41", "40", "1.02", "0.2", "0.19"]  # ×40 pendant 3 relevés puis retour
    ticks = [(NOW + i, D(p), None) for i, p in enumerate(path)]
    kept = [t[1] for t in drop_glitches(ticks)]
    assert D("40") not in kept and D("41") not in kept  # saut temporaire qui revient : aberrant
    assert D("0.2") in kept and D("0.19") in kept  # vraie chute qui ne revient pas : conservée



def test_h2_trend_follower_rule_and_incomplete_tracking(tmp_path: Path) -> None:
    from memepilot.paper.lab import H2_EXITS, _h2, lab_trades
    assert _h2({"signals": {"momentum": {"score": 65, "confidence": 1}}, "candidate": {"age_min": 400}})
    assert not _h2({"signals": {"momentum": {"score": 65, "confidence": 1}}, "candidate": {"age_min": 120}})
    lg = Ledger(tmp_path / "h2.db")
    f = {"signals": {"momentum": {"score": 70, "confidence": 1}}, "candidate": {"age_min": 500}}
    runner = lg.add_shadow("R", "R", "labo", "", NOW, None, None, 60.0, f)  # +60 % puis rechute : trailing
    for i, p in enumerate(["1", "1.2", "1.6", "1.3", "1.1"]):
        lg.record_shadow_tick(runner, NOW + 5 + 600 * i, D(p), D("80000"))
    lg.record_shadow_tick(runner, NOW + 5 + 4 * 3600, D("1.0"), D("80000"))  # suivi complet (4 h)
    short = lg.add_shadow("S", "S", "labo", "", NOW, None, None, 60.0, f)  # 10 min seulement, même s'il chute
    for i, p in enumerate(["1", "1.05", "0.70"]):
        lg.record_shadow_tick(short, NOW + 5 + 300 * i, D(p), D("80000"))
    trades = lab_trades(lg, H2_EXITS, rule=_h2)
    assert len(trades) == 1 and trades[0][2] > 0  # sommet +60 %, vendu à 1.1 (sous 1.6 × 0,75), suivi court écarté
    lg.close()
