"""D-037 : chien de garde (arrêt, veille, cycles ou surveillance bloqués)."""

from __future__ import annotations

from pathlib import Path

from memepilot.reporting.watchdog import (
    Watchdog,
    ping_healthcheck,
    read_heartbeat,
    startup_message,
    write_heartbeat,
)

T = 1_800_000_000.0


def test_heartbeat_file_and_startup_message(tmp_path: Path) -> None:
    hb = tmp_path / "heartbeat.json"
    assert read_heartbeat(hb) is None and startup_message(None, T) is None
    write_heartbeat(hb, T)
    assert read_heartbeat(hb) == T
    assert startup_message(T, T + 60) is None  # simple redémarrage rapide
    msg = startup_message(T, T + 3 * 3600 + 600)
    assert msg is not None and "ARRÊTÉ" in msg and "3 h 10" in msg


def test_sleep_gap_and_stalls_alert_once_then_recover() -> None:
    w = Watchdog(last_beat=T)
    assert w.check(T + 30, T, T + 20, T + 29, 1) == []
    gap = w.check(T + 30 + 900, T, T + 900, T + 925, 1)
    assert len(gap) == 1 and "pause détectée" in gap[0]
    now = T + 2000
    alerts = w.check(now, T, now - 400, now - 90, 2)  # pas de cycle depuis 6 min + positions non surveillées
    assert any("aucun cycle" in a for a in alerts) and any("NON surveillée" in a for a in alerts)
    assert w.check(now + 30, T, now - 370, now - 60, 2) == []  # pas de répétition
    back = w.check(now + 60, T, now + 50, now + 59, 2)
    assert any("ont repris" in a for a in back) and any("a repris" in a for a in back)
    assert w.check(now + 90, T, now + 80, None, 0) == []  # aucune position : pas d'alerte de surveillance


def test_healthcheck_refuses_non_https() -> None:
    assert ping_healthcheck("http://example.com/ping") is False
