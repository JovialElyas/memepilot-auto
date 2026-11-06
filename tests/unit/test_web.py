"""Interface live : serveur en lecture seule et instantané JSON."""

import asyncio
import json
from pathlib import Path

from memepilot.engine.paper import PaperEngine
from memepilot.web.server import LiveServer
from memepilot.web.snapshot import EventLog, build_snapshot

from .test_paper import LIM, PricedClient


def test_server_is_read_only_and_routes() -> None:
    srv = LiveServer(lambda: {"ok": True, "x": 1})
    assert srv.host == "127.0.0.1"
    assert srv.route("POST", "/api/state").startswith(b"HTTP/1.1 405")
    assert srv.route("GET", "/../../etc/passwd").startswith(b"HTTP/1.1 404")
    r = srv.route("GET", "/api/state?t=1")
    assert r.startswith(b"HTTP/1.1 200") and b"no-store" in r and r.endswith(b'{"ok": true, "x": 1}')
    page = srv.route("GET", "/")
    assert b"text/html" in page and b"/api/state" in page and b"X-Frame-Options: DENY" in page


async def test_server_serves_over_tcp() -> None:
    srv = LiveServer(lambda: {"hello": "monde"}, port=0)
    await srv.start()
    assert srv._server is not None
    port = srv._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /api/state HTTP/1.1\r\nHost: x\r\n\r\n")
    await writer.drain()
    raw = await reader.read()
    writer.close()
    await srv.stop()
    assert json.loads(raw.split(b"\r\n\r\n", 1)[1]) == {"hello": "monde"}


async def test_snapshot_contains_live_position_and_candidates(tmp_path: Path) -> None:
    client = PricedClient(["1.0", "1.2"])
    from memepilot.paper.exits import ExitConfig
    eng = PaperEngine(client, str(tmp_path / "p.db"), LIM, exits=ExitConfig(take_profit=None))
    r = await eng.run_cycle()
    client.cycle = 1
    m = await eng.monitor(5)
    log = EventLog()
    log.add("buy", "ACHAT papier GOOD")
    snap = build_snapshot(eng, m.state, r.cycle, r.verdicts, {"key": "test", "monitor_s": 5}, log)
    json.dumps(snap, default=str)  # sérialisable
    pos = snap["positions"][0]
    assert pos["symbol"] == "GOOD" and pos["pnl_pct"] is not None and pos["pnl_pct"] > 0
    # D-030 : sommet ≈ +19 % → gain protégé, jamais sous le plancher de +8 % ; moitié pas encore vendue
    assert pos["sell_at_pct"] is not None and pos["sell_at_pct"] >= 8.0 and not pos["partial_done"]
    assert snap["exits"]["partial_at_pct"] == 50.0 and snap["exits"]["tight_dd_pct"] == 7.0
    assert snap["candidates"][0]["propose"] and snap["candidates"][0]["guardian"]["ok"]
    assert snap["kpis"]["slots"] == 1 and snap["meta"]["threshold"] == 70.0
    assert snap["log"][0]["kind"] == "buy"
    eng.close()


def _load_script(name: str):  # type: ignore[no-untyped-def]
    import importlib.util
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(name, root / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backoff_doubles_after_blocked_cycles_and_is_capped() -> None:
    pt = _load_script("paper_trade")
    assert pt.backoff_interval(30, 0) == 30
    assert pt.backoff_interval(30, 1) == 60
    assert pt.backoff_interval(30, 2) == 120
    assert pt.backoff_interval(30, 10) == 180  # plafond


def test_last_cycle_cache_round_trip_and_corruption(tmp_path: Path) -> None:
    pt = _load_script("paper_trade")
    p = tmp_path / "last_cycle.json"
    assert pt.load_cached_view(p) is None
    pt.save_cached_view(p, {"candidates": [{"symbol": "A"}], "scan": {"seen": 3}, "kpis": {}})
    got = pt.load_cached_view(p)
    assert got["candidates"][0]["symbol"] == "A" and got["scan"]["seen"] == 3
    p.write_text("{pas du json", encoding="utf-8")
    assert pt.load_cached_view(p) is None


async def test_snapshot_uses_cached_cycle_when_no_complete_cycle(tmp_path: Path) -> None:
    eng = PaperEngine(PricedClient(["1.0"]), str(tmp_path / "p.db"), LIM)
    cached = {"ts": 123.0, "candidates": [{"symbol": "OLD"}], "scan": {"seen": 9}}
    snap = build_snapshot(eng, None, None, {}, {"key": "t"}, EventLog(), cached=cached)
    assert snap["candidates"][0]["symbol"] == "OLD" and snap["meta"]["from_cache"]
    assert snap["meta"]["data_cycle_ts"] == 123.0
    eng.close()


def test_port_in_use_detects_running_bot() -> None:
    import socket
    pt = _load_script("paper_trade")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        assert pt.port_in_use("127.0.0.1", s.getsockname()[1])
    assert not pt.port_in_use("127.0.0.1", 0)
