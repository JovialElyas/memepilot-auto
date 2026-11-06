"""Persistance de l'état de risque (SQLite, mode WAL) — SPEC F-23/F-24, G-18, G-20, G-22.

- Une seule ligne d'état (JSON) + un journal d'événements append-only.
- Écriture transactionnelle : un crash ne laisse jamais un état à moitié écrit.
- L'empreinte des limites est enregistrée à l'initialisation. En mode `live`, un écart
  empêche le démarrage ; en mode `paper`, il est journalisé et l'état papier est réinitialisé.
- Le LOCK ne se lève QUE par `admin_unlock`, appelé par `scripts/risk_admin.py` avec une phrase
  de confirmation tapée à la main (test d'architecture : aucun autre module ne l'appelle).
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from memepilot.config.limits import Limits
from memepilot.risk.state import OpenPosition, PendingOrder, RiskState, new_state

UNLOCK_PHRASE = "JE COMPRENDS QUE LA PERTE MAXIMALE A ETE ATTEINTE"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_state (id INTEGER PRIMARY KEY CHECK (id = 1), mode TEXT NOT NULL,
  state_json TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS risk_events (ts REAL NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
"""


class LimitsChangedError(RuntimeError):
    pass


class UnlockRefused(RuntimeError):
    pass


def _encode(s: RiskState) -> str:
    d = asdict(s)
    d["day"] = s.day.isoformat()
    for p in d["positions"]:
        p["opened_day"] = p["opened_day"].isoformat()
    return json.dumps(d, default=str, ensure_ascii=False)


def _decode(raw: str) -> RiskState:
    d: dict[str, Any] = json.loads(raw)
    dec = Decimal
    return RiskState(
        day=date.fromisoformat(d["day"]), initial_capital_usd=dec(d["initial_capital_usd"]),
        realized_equity_usd=dec(d["realized_equity_usd"]), realized_equity_low_usd=dec(d["realized_equity_low_usd"]),
        realized_pnl_today_usd=dec(d["realized_pnl_today_usd"]), trades_today=int(d["trades_today"]),
        positions=tuple(OpenPosition(p["token"], dec(p["cost_usd"]), dec(p["entry_fee_usd"]),
                                     dec(p["est_exit_fee_usd"]), date.fromisoformat(p["opened_day"]))
                        for p in d["positions"]),
        pending=tuple(PendingOrder(o["token"], dec(o["reserved_usd"])) for o in d["pending"]),
        locked=bool(d["locked"]), lock_reason=d["lock_reason"], halted=bool(d["halted"]),
        limits_fingerprint=d["limits_fingerprint"], events=tuple(d.get("events", ())),
    )


class RiskStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _event(self, kind: str, detail: str) -> None:
        self._db.execute("INSERT INTO risk_events VALUES (?, ?, ?)", (time.time(), kind, detail))

    def load_or_init(self, limits: Limits, mode: str, today: date) -> RiskState:
        row = self._db.execute("SELECT mode, state_json FROM risk_state WHERE id = 1").fetchone()
        fp = limits.fingerprint()
        if row is None:
            s = new_state(limits.initial_capital_usd, today, fp)
            self.save(s, mode, event=("init", f"capital {limits.initial_capital_usd} $, limites {fp[:12]}"))
            return s
        stored_mode, raw = row
        s = _decode(raw)
        if stored_mode != mode:
            raise LimitsChangedError(f"base créée en mode {stored_mode}, ouverte en mode {mode} : refus")
        if s.limits_fingerprint != fp:
            if mode == "live":
                raise LimitsChangedError("les limites ont changé depuis l'initialisation : démarrage live refusé")
            if s.initial_capital_usd == limits.initial_capital_usd:
                # D-032 : en papier, un changement de limites (ex. trades/jour) GARDE l'état : positions ouvertes,
                # capital réalisé et surtout les pertes déjà consommées (jamais remises à zéro par une config).
                self._event("limits_changed", f"{s.limits_fingerprint[:12]} → {fp[:12]} (papier : état conservé)")
                s = replace(s, limits_fingerprint=fp)
            else:
                self._event("limits_changed", f"{s.limits_fingerprint[:12]} → {fp[:12]} "
                                              "(papier : capital initial changé, réinitialisation)")
                s = new_state(limits.initial_capital_usd, today, fp)
            self.save(s, mode)
        return s

    def save(self, s: RiskState, mode: str, event: tuple[str, str] | None = None) -> None:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                "INSERT INTO risk_state (id, mode, state_json, updated_at) VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at",
                (mode, _encode(s), time.time()),
            )
            if event:
                self._event(*event)
            if s.locked:
                self._event("lock", s.lock_reason)
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            raise

    def events(self, limit: int = 20) -> list[tuple[float, str, str]]:
        return list(self._db.execute(
            "SELECT ts, kind, detail FROM risk_events ORDER BY ts DESC LIMIT ?", (limit,)))

    def admin_unlock(self, mode: str, typed_phrase: str, operator_note: str) -> RiskState:
        """Déverrouillage MANUEL uniquement. Ne remet PAS les pertes à zéro : seul le verrou est levé."""
        if typed_phrase.strip() != UNLOCK_PHRASE:
            raise UnlockRefused("phrase de confirmation incorrecte")
        if not operator_note.strip():
            raise UnlockRefused("une justification écrite est obligatoire")
        row = self._db.execute("SELECT state_json FROM risk_state WHERE id = 1").fetchone()
        if row is None:
            raise UnlockRefused("aucun état de risque")
        s = replace(_decode(row[0]), locked=False, lock_reason="")
        self.save(s, mode, event=("unlock", operator_note.strip()))
        return s
