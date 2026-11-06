import json
import time
from pathlib import Path
from typing import Any

from memepilot.engine.cycle import CycleConfig, analyze_cycle, append_journal
from memepilot.engine.dashboard import history_entry, render
from memepilot.gmgn.models import TrendingRow
from memepilot.gmgn.read_client import ArgValue, GmgnRateLimited, GmgnReadClient, GmgnResult, unwrap
from memepilot.scanner.prefilter import Candidate
from memepilot.signals.base import SignalReport
from memepilot.strategy.hybrid import Decision, RiskClass, decide, risk_class

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "gmgn"
A = "66auXVTRw9ZekMeB8h5jdJS9WhMRVz8zfMyzqeinpump"
SOL_MINT = "So11111111111111111111111111111111111111112"
W = ["DYAn4XpAkN5mhiXkRB7dGq4Jadnx6XYgu8L5b3WGhbrt", "2rmaFtFZmJtLXdPgRvk417RLc9GBD3jGbAqtttmfG3AF",
     "4euyaqBe45J5dniAcNARERUo7B1wG96riG21sEpNndfU"]

ROW: dict[str, Any] = {
    "address": A, "symbol": "GOOD", "liquidity": 80_000, "rug_ratio": 0.05, "renounced_mint": 1,
    "renounced_freeze_account": 1, "is_wash_trading": False, "top_10_holder_rate": 0.15,
    "dev_team_hold_rate": 0, "bundler_rate": 0.05, "rat_trader_amount_rate": 0.01, "smart_degen_count": 8,
    "renowned_count": 2, "buys": 70, "sells": 30, "price_change_percent5m": 10, "price_change_percent1h": 40,
    "creation_timestamp": int(time.time()) - 6 * 3600,
}


def cand(**over: Any) -> Candidate:
    return Candidate.from_trending(TrendingRow.model_validate({**ROW, **over}))


def sig(module: str, score: float, conf: float = 1.0, vetoes: tuple[str, ...] = ()) -> SignalReport:
    return SignalReport(module, score, conf, (), vetoes)


# ── stratégie hybride ─────────────────────────────────────────────────────
def test_propose_requires_two_confirmations_and_threshold() -> None:
    d = decide(cand(), [sig("momentum", 80), sig("smart_money", 85), sig("early_token", 0, 0)])
    assert d.decision is Decision.PROPOSE and d.exit_plan and d.size_fraction == 1.0


def test_single_strong_module_is_not_enough() -> None:
    d = decide(cand(), [sig("momentum", 95), sig("smart_money", 40), sig("early_token", 0, 0)])
    assert d.decision is Decision.NO_TRADE and "confirmation croisée" in d.reasons[0]


def test_any_veto_blocks() -> None:
    d = decide(cand(), [sig("momentum", 90), sig("smart_money", 90, vetoes=("sortie",))])
    assert d.decision is Decision.NO_TRADE and "veto" in d.reasons[0]


def test_low_confidence_score_is_discounted() -> None:
    # Cas réel MOGZ : 56×0,2 et 100×0,4 affichaient 88 avant correction.
    d = decide(cand(), [sig("momentum", 55.6, 0.2), sig("smart_money", 100, 0.4), sig("early_token", 0, 0)])
    assert d.score < 40 and d.decision is Decision.NO_TRADE


def test_high_risk_never_proposed_and_medium_halved() -> None:
    strong = [sig("momentum", 90), sig("smart_money", 90)]
    assert decide(cand(rug_ratio=0.25), strong).decision is Decision.NO_TRADE
    m = decide(cand(liquidity=30_000), strong)
    assert m.decision is Decision.PROPOSE and m.risk_class is RiskClass.MEDIUM and m.size_fraction == 1.0  # D-033


def test_no_data_means_no_trade() -> None:
    assert decide(cand(), [sig("momentum", 0, 0)]).decision is Decision.NO_TRADE


def test_risk_class_missing_rug_is_high() -> None:
    assert risk_class(cand(rug_ratio=None)) is RiskClass.HIGH


# ── cycle complet (faux client) ───────────────────────────────────────────
class FakeClient(GmgnReadClient):
    def __init__(self, fail: set[tuple[str, ...]] | None = None) -> None:
        super().__init__("gmgn-cli")
        self.fail = fail or set()
        self.seen: list[tuple[str, ...]] = []

    async def call(self, command: tuple[str, ...], cache_ttl_s: float | None = None,
                   **options: ArgValue) -> GmgnResult:
        self.seen.append(command)
        self.calls += 1
        if command in self.fail:
            raise GmgnRateLimited("RATE_LIMIT_BANNED", 20)
        now = int(time.time())
        data: Any
        if command == ("token", "info") and options.get("address") != SOL_MINT:
            # Cohérent avec ROW : +10 % sur 5 min (< seuil de pic 12 %, D-029), +40 % sur 1 h, 70 % d achats
            data = {"address": options["address"], "symbol": "GOOD", "decimals": 6, "liquidity": "80000",
                    "price": {"price": "1.0", "price_5m": "0.909", "price_1h": "0.714", "buys_5m": 70, "sells_5m": 30}}
        elif command == ("token", "info"):
            data = unwrap(json.loads((FIX / "token_info_sol.json").read_text(encoding="utf-8")))
        elif command == ("market", "trending"):
            data = {"rank": [ROW]}
        elif command == ("market", "trenches"):
            data = {"completed": []}
        elif command == ("token", "security"):
            data = {"address": options["address"], "renounced_mint": 1, "renounced_freeze_account": 1}
        elif command == ("token", "pool"):
            data = {"address": options["address"], "liquidity": "80000"}
        elif command == ("track", "smartmoney"):
            data = {"list": [{"transaction_hash": f"h{i}", "maker": w, "base_address": A, "side": "buy",
                              "amount_usd": 800, "timestamp": now - 60} for i, w in enumerate(W)]}
        elif command == ("track", "kol"):
            data = {"list": []}
        elif command == ("market", "kline"):
            data = {"list": [{"time": (now - 900 + 60 * i) * 1000, "open": 1 + 0.01 * i, "close": 1 + 0.01 * (i + 1),
                              "high": 1 + 0.01 * (i + 1), "low": 1 + 0.01 * i, "volume": "100"} for i in range(15)]}
        else:
            raise AssertionError(command)
        return GmgnResult(command, data, 1.0, "", time.time())


async def test_cycle_end_to_end_proposes_clean_cluster(tmp_path: Path) -> None:
    client = FakeClient()
    rep = await analyze_cycle(client, CycleConfig())
    assert rep.feed_ok and rep.klines_fetched == 1
    assert len(rep.proposals) == 1, [d.reasons for d in rep.decisions]
    assert not any(c[0] in ("swap", "order", "multi-swap", "cooking") for c in client.seen)
    append_journal(tmp_path / "j.jsonl", rep)
    line = json.loads((tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert line["decision"] == "PROPOSE" and line["exit_plan"]["stop_loss_pct"] == 15.0  # D-040
    page = render(rep, key_label="test", refresh_s=60, history=[history_entry(rep, client.calls)])
    assert "PROPOSE" in page and "Mode PAPIER" in page


async def test_cycle_survives_feed_failure_with_lower_confidence() -> None:
    rep = await analyze_cycle(FakeClient(fail={("track", "smartmoney")}), CycleConfig())
    assert not rep.feed_ok and rep.errors
    assert rep.proposals == []  # sans flux temps réel, pas assez de confiance pour proposer


async def test_cycle_stops_when_scan_rate_limited() -> None:
    client = FakeClient(fail={("market", "trending")})
    rep = await analyze_cycle(client, CycleConfig())
    assert rep.decisions == []
    # D-036 : le flux smart money est lu AVANT le scan, quand le quota GMGN est encore plein
    assert client.seen.index(("track", "smartmoney")) < client.seen.index(("market", "trending"))


def test_dashboard_escapes_external_text() -> None:
    from memepilot.engine.cycle import CycleReport
    from memepilot.scanner.pipeline import ScanReport

    c = cand(symbol="<script>alert(1)</script>")
    d = decide(c, [sig("momentum", 10)])
    rep = CycleReport(scan=ScanReport(started_at=0.0, survivors=[c]), decisions=[d], finished_at=time.time())
    page = render(rep, key_label="x", refresh_s=60, history=[])
    assert "<script>alert" not in page and "&lt;script&gt;" in page


async def test_guardian_sizes_proposal_and_dashboard_shows_it() -> None:
    from datetime import date
    from decimal import Decimal as D

    from memepilot.config.limits import load_limits
    from memepilot.engine.cycle import guard_proposals
    from memepilot.risk.state import new_state, set_halt

    lim = load_limits(Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "limits_test.yaml")
    today = date(2026, 11, 2)
    rep = await analyze_cycle(FakeClient(), CycleConfig())
    st = new_state(lim.initial_capital_usd, today, lim.fingerprint())
    v = guard_proposals(rep, st, lim, today)
    assert v[A].approved and v[A].size_usd == D("4.60")
    page = render(rep, key_label="t", refresh_s=60, history=[], risk_state=st, limits=lim, verdicts=v)
    assert "Risk Guardian" in page and "4.60 $" in page
    halted = guard_proposals(rep, set_halt(st, True, "test"), lim, today)
    assert not halted[A].approved
