"""Ledger persistant (SPEC F-40 à F-42, ARCHITECTURE §6) — SQLite, mode WAL.

Chaque décision, ordre, exécution, position et événement est enregistré. Les ordres portent une
clé d'idempotence UNIQUE : un même ordre ne peut pas être créé deux fois (F-31).
Les montants sont stockés en texte (Decimal exact), les horodatages en epoch (s).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from memepilot.paper.broker import PaperFill
from memepilot.paper.exits import PositionView

PARTIAL_REASON = "prise de profit partielle"

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, ts REAL, token TEXT, symbol TEXT, score REAL,
  risk_class TEXT, decision TEXT, strategy TEXT, reasons TEXT, signals TEXT, guardian TEXT);
CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, idem_key TEXT UNIQUE NOT NULL, ts REAL, token TEXT,
  side TEXT, size_usd TEXT, state TEXT, decision_id INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS fills (id INTEGER PRIMARY KEY, order_id INTEGER, ts REAL, token TEXT, side TEXT,
  mid_price TEXT, exec_price TEXT, qty TEXT, gross_usd TEXT, fee_usd TEXT, slippage TEXT);
CREATE TABLE IF NOT EXISTS positions (id INTEGER PRIMARY KEY, token TEXT, symbol TEXT, strategy TEXT,
  status TEXT, opened_ts REAL, closed_ts REAL, qty TEXT, entry_price TEXT, cost_usd TEXT, entry_fee_usd TEXT,
  entry_liquidity TEXT, peak_price TEXT, missing_cycles INTEGER DEFAULT 0, exit_price TEXT, proceeds_usd TEXT,
  exit_fee_usd TEXT, pnl_usd TEXT, exit_reason TEXT, decision_id INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_per_token ON positions(token) WHERE status = 'open';
CREATE TABLE IF NOT EXISTS equity (ts REAL, realized_usd TEXT, unrealized_usd TEXT, exposure_usd TEXT);
CREATE TABLE IF NOT EXISTS events (ts REAL, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS price_ticks (token TEXT, opened_ts REAL, ts REAL, price TEXT, liquidity TEXT);
CREATE INDEX IF NOT EXISTS ticks_by_position ON price_ticks(token, opened_ts, ts);
CREATE TABLE IF NOT EXISTS shadow (id INTEGER PRIMARY KEY, token TEXT, symbol TEXT, kind TEXT, reason TEXT,
  ts REAL, price TEXT, liquidity TEXT, score REAL);
CREATE TABLE IF NOT EXISTS shadow_ticks (shadow_id INTEGER, ts REAL, price TEXT, liquidity TEXT);
CREATE INDEX IF NOT EXISTS shadow_ticks_by_id ON shadow_ticks(shadow_id, ts);
"""


def _d(v: Any) -> Decimal | None:
    return None if v is None else Decimal(str(v))


@dataclass(frozen=True)
class OpenPositionRow(PositionView):
    symbol: str | None
    strategy: str
    qty: Decimal
    cost_usd: Decimal
    entry_fee_usd: Decimal
    partial_done: bool = False  # moitié déjà vendue (D-030)


@dataclass(frozen=True)
class ClosedTrade:
    token: str
    symbol: str | None
    strategy: str
    opened_ts: float
    closed_ts: float
    cost_usd: Decimal
    proceeds_usd: Decimal
    fees_usd: Decimal
    pnl_usd: Decimal
    exit_reason: str
    profile: str = "strict"
    strict_ok: bool = True
    data_source: str = "gmgn"


class Ledger:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Ajoute les colonnes apparues après la création de la base (aucune donnée perdue)."""
        cols = {r[1] for r in self._x("PRAGMA table_info(positions)")}
        if "profile" not in cols:
            self._x("ALTER TABLE positions ADD COLUMN profile TEXT DEFAULT 'strict'")
        if "strict_ok" not in cols:
            self._x("ALTER TABLE positions ADD COLUMN strict_ok INTEGER DEFAULT 1")
        if "data_source" not in cols:  # D-024 : gmgn | relais | mixte (données qui ont motivé l'achat)
            self._x("ALTER TABLE positions ADD COLUMN data_source TEXT DEFAULT 'gmgn'")
        shadow_cols = {r[1] for r in self._x("PRAGMA table_info(shadow)")}
        if "features" not in shadow_cols:  # D-041 : caractéristiques au moment de la décision (laboratoire)
            self._x("ALTER TABLE shadow ADD COLUMN features TEXT")
        if "partial_done" not in cols:  # D-030 : prise de profit partielle déjà faite sur cette position
            self._x("ALTER TABLE positions ADD COLUMN partial_done INTEGER DEFAULT 0")

    def close(self) -> None:
        self._db.close()

    def _x(self, sql: str, args: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self._db.execute(sql, args)

    # ── écritures ─────────────────────────────────────────────────────────
    def event(self, ts: float, kind: str, detail: str) -> None:
        self._x("INSERT INTO events VALUES (?,?,?)", (ts, kind, detail))

    def record_decision(self, ts: float, token: str, symbol: str | None, score: float, risk_class: str,
                        decision: str, strategy: str, reasons: list[str], signals: dict[str, Any],
                        guardian: dict[str, Any] | None) -> int:
        cur = self._x("INSERT INTO decisions (ts,token,symbol,score,risk_class,decision,strategy,reasons,signals,"
                      "guardian) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (ts, token, symbol, score, risk_class, decision, strategy,
                       json.dumps(reasons, ensure_ascii=False), json.dumps(signals, ensure_ascii=False),
                       json.dumps(guardian) if guardian else None))
        return int(cur.lastrowid or 0)

    def create_order(self, idem_key: str, ts: float, token: str, side: str, size_usd: Decimal,
                     decision_id: int | None) -> int:
        """Lève sqlite3.IntegrityError si la clé d'idempotence existe déjà : jamais de double ordre."""
        cur = self._x("INSERT INTO orders (idem_key,ts,token,side,size_usd,state,decision_id) VALUES (?,?,?,?,?,?,?)",
                      (idem_key, ts, token, side, str(size_usd), "CREATED", decision_id))
        return int(cur.lastrowid or 0)

    def set_order_state(self, order_id: int, state: str, error: str | None = None) -> None:
        self._x("UPDATE orders SET state = ?, error = ? WHERE id = ?", (state, error, order_id))

    def fail_interrupted_orders(self, keep: set[int] | None = None) -> int:
        """Ordres restés à CREATED (crash entre la création et l'exécution) → FAILED.

        `keep` : ordres d'achat en cours de confirmation (D-030), à ne pas toucher.
        """
        ids = [r[0] for r in self._x("SELECT id FROM orders WHERE state='CREATED'") if r[0] not in (keep or set())]
        for oid in ids:
            self._x("UPDATE orders SET state='FAILED', error='interrompu (redémarrage)' WHERE id = ?", (oid,))
        return len(ids)

    def record_fill(self, order_id: int, ts: float, f: PaperFill) -> None:
        self._x("INSERT INTO fills (order_id,ts,token,side,mid_price,exec_price,qty,gross_usd,fee_usd,slippage) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (order_id, ts, f.token, f.side, str(f.mid_price_usd), str(f.exec_price_usd), str(f.qty),
                 str(f.gross_usd), str(f.fee_usd), str(f.slippage)))

    def open_position(self, ts: float, token: str, symbol: str | None, strategy: str, fill: PaperFill,
                      entry_liquidity: Decimal | None, decision_id: int | None, profile: str = "strict",
                      strict_ok: bool = True, data_source: str = "gmgn") -> None:
        self._x("INSERT INTO positions (token,symbol,strategy,status,opened_ts,qty,entry_price,cost_usd,"
                "entry_fee_usd,entry_liquidity,peak_price,decision_id,profile,strict_ok,data_source) "
                "VALUES (?,?,?,'open',?,?,?,?,?,?,?,?,?,?,?)",
                (token, symbol, strategy, ts, str(fill.qty), str(fill.exec_price_usd), str(fill.gross_usd),
                 str(fill.fee_usd), None if entry_liquidity is None else str(entry_liquidity),
                 str(fill.exec_price_usd), decision_id, profile, int(strict_ok), data_source))

    def record_tick(self, token: str, opened_ts: float, ts: float, price: Decimal | None,
                    liquidity: Decimal | None) -> None:
        """Prix observé d'une position ouverte (toutes les ~5 s) : matière du rejeu des règles de sortie (D-026)."""
        self._x("INSERT INTO price_ticks (token, opened_ts, ts, price, liquidity) VALUES (?,?,?,?,?)",
                (token, opened_ts, ts, None if price is None else str(price),
                 None if liquidity is None else str(liquidity)))

    def ticks(self, token: str, opened_ts: float) -> list[tuple[float, Decimal | None, Decimal | None]]:
        rows = self._x("SELECT ts, price, liquidity FROM price_ticks WHERE token = ? AND opened_ts = ? ORDER BY ts",
                       (token, opened_ts)).fetchall()
        return [(r[0], _d(r[1]), _d(r[2])) for r in rows]

    def track_position(self, token: str, peak_price: Decimal, missing_cycles: int) -> None:
        self._x("UPDATE positions SET peak_price = ?, missing_cycles = ? WHERE token = ? AND status = 'open'",
                (str(peak_price), missing_cycles, token))

    def close_position(self, ts: float, token: str, fill: PaperFill, pnl_usd: Decimal, reason: str) -> None:
        cur = self._x("UPDATE positions SET status='closed', closed_ts=?, exit_price=?, proceeds_usd=?, "
                      "exit_fee_usd=?, pnl_usd=?, exit_reason=? WHERE token = ? AND status = 'open'",
                      (ts, str(fill.exec_price_usd), str(fill.gross_usd), str(fill.fee_usd), str(pnl_usd), reason,
                       token))
        if cur.rowcount != 1:
            raise RuntimeError(f"aucune position ouverte à fermer pour {token}")

    def partial_close(self, ts: float, token: str, fill: PaperFill, part_cost: Decimal, part_fee: Decimal,
                      pnl_usd: Decimal, reason: str) -> None:
        """Vente partielle (D-030) : la part vendue devient une ligne fermée ; la position ouverte est réduite."""
        row = self._x("SELECT id,symbol,strategy,opened_ts,qty,entry_price,cost_usd,entry_fee_usd,entry_liquidity,"
                      "peak_price,decision_id,profile,strict_ok,data_source FROM positions "
                      "WHERE token = ? AND status = 'open'", (token,)).fetchone()
        if row is None:
            raise RuntimeError(f"aucune position ouverte à réduire pour {token}")
        pid, sym, strat, opened, qty, entry, cost, fee, liq, peak, dec, prof, sok, src = row
        self._x("INSERT INTO positions (token,symbol,strategy,status,opened_ts,closed_ts,qty,entry_price,cost_usd,"
                "entry_fee_usd,entry_liquidity,peak_price,exit_price,proceeds_usd,exit_fee_usd,pnl_usd,exit_reason,"
                "decision_id,profile,strict_ok,data_source,partial_done) "
                "VALUES (?,?,?,'closed',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (token, sym, strat, opened, ts, str(fill.qty), entry, str(part_cost), str(part_fee), liq, peak,
                 str(fill.exec_price_usd), str(fill.gross_usd), str(fill.fee_usd), str(pnl_usd), reason, dec, prof,
                 sok, src))
        self._x("UPDATE positions SET qty = ?, cost_usd = ?, entry_fee_usd = ?, partial_done = 1 WHERE id = ?",
                (str(Decimal(qty) - fill.qty), str(Decimal(cost) - part_cost), str(Decimal(fee) - part_fee), pid))

    # ── suivi des tokens refusés (D-037) ─────────────────────────────────
    def add_shadow(self, token: str, symbol: str | None, kind: str, reason: str, ts: float, price: Decimal | None,
                   liquidity: Decimal | None, score: float | None, features: dict[str, Any] | None = None) -> int:
        cur = self._x("INSERT INTO shadow (token,symbol,kind,reason,ts,price,liquidity,score,features) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (token, symbol, kind, reason[:200], ts, None if price is None else str(price),
                       None if liquidity is None else str(liquidity), score,
                       None if features is None else json.dumps(features, ensure_ascii=False)))
        return int(cur.lastrowid or 0)

    def record_shadow_tick(self, shadow_id: int, ts: float, price: Decimal | None,
                           liquidity: Decimal | None) -> None:
        self._x("INSERT INTO shadow_ticks VALUES (?,?,?,?)", (shadow_id, ts, None if price is None else str(price),
                                                              None if liquidity is None else str(liquidity)))

    def shadows(self, t0: float = 0.0, t1: float = 1e12, kind: str | None = None) -> list[dict[str, Any]]:
        rows = self._x("SELECT id,token,symbol,kind,reason,ts,price,liquidity,score,features FROM shadow "
                       "WHERE ts >= ? AND ts < ? ORDER BY ts", (t0, t1)).fetchall()
        keys = ("id", "token", "symbol", "kind", "reason", "ts", "price", "liquidity", "score", "features")
        out = []
        for r in rows:
            d = dict(zip(keys, r, strict=True))
            if kind is not None and d["kind"] != kind:
                continue
            d["price"], d["liquidity"] = _d(d["price"]), _d(d["liquidity"])
            d["features"] = json.loads(d["features"]) if d["features"] else {}
            out.append(d)
        return out

    def shadow_ticks(self, shadow_id: int) -> list[tuple[float, Decimal | None, Decimal | None]]:
        rows = self._x("SELECT ts, price, liquidity FROM shadow_ticks WHERE shadow_id = ? ORDER BY ts",
                       (shadow_id,)).fetchall()
        return [(r[0], _d(r[1]), _d(r[2])) for r in rows]

    def snapshot_equity(self, ts: float, realized: Decimal, unrealized: Decimal, exposure: Decimal) -> None:
        self._x("INSERT INTO equity VALUES (?,?,?,?)", (ts, str(realized), str(unrealized), str(exposure)))

    # ── lectures ──────────────────────────────────────────────────────────
    def open_positions(self) -> list[OpenPositionRow]:
        rows = self._x("SELECT token,entry_price,entry_liquidity,peak_price,opened_ts,missing_cycles,symbol,strategy,"
                       "qty,cost_usd,entry_fee_usd,partial_done FROM positions WHERE status='open' "
                       "ORDER BY opened_ts").fetchall()
        return [OpenPositionRow(token=r[0], entry_price=Decimal(r[1]), entry_liquidity=_d(r[2]),
                                peak_price=Decimal(r[3]), opened_ts=r[4], missing_cycles=r[5], symbol=r[6],
                                strategy=r[7], qty=Decimal(r[8]), cost_usd=Decimal(r[9]),
                                entry_fee_usd=Decimal(r[10]), partial_done=bool(r[11])) for r in rows]

    def last_loss_ts(self, token: str) -> float | None:
        """Heure de la dernière sortie PERDANTE sur ce token (None si aucune)."""
        rows = self._x("SELECT closed_ts, pnl_usd FROM positions WHERE token = ? AND status = 'closed' "
                       "ORDER BY closed_ts DESC LIMIT 5", (token,)).fetchall()
        for ts, pnl in rows:
            if pnl is not None and Decimal(pnl) < 0:
                return float(ts)
        return None

    def closed_for_replay(self) -> list[dict[str, Any]]:
        """Positions fermées avec ce qu'il faut pour rejouer d'autres règles de sortie sur leurs prix réels.

        Une vente partielle (D-030) est refondue dans sa position : le rejeu repart de la position entière.
        """
        rows = self._x("SELECT token,symbol,opened_ts,closed_ts,entry_price,entry_liquidity,qty,cost_usd,"
                       "entry_fee_usd,pnl_usd,exit_reason,partial_done FROM positions WHERE status='closed' "
                       "ORDER BY opened_ts, closed_ts").fetchall()
        keys = ("token", "symbol", "opened_ts", "closed_ts", "entry_price", "entry_liquidity", "qty", "cost_usd",
                "entry_fee_usd", "pnl_usd", "exit_reason")
        out: list[dict[str, Any]] = []
        parts: dict[tuple[str, float], list[dict[str, Any]]] = {}
        for r in rows:
            d = dict(zip(keys, r[:-1], strict=True))
            for k in ("entry_price", "entry_liquidity", "qty", "cost_usd", "entry_fee_usd", "pnl_usd"):
                d[k] = _d(d[k])
            if str(d["exit_reason"] or "").startswith(PARTIAL_REASON):
                parts.setdefault((d["token"], d["opened_ts"]), []).append(d)
            else:
                out.append(d)
        for d in out:
            for part in parts.get((d["token"], d["opened_ts"]), []):
                for k in ("qty", "cost_usd", "entry_fee_usd", "pnl_usd"):
                    d[k] = (d[k] or Decimal(0)) + (part[k] or Decimal(0))
        return out

    def closed_trades(self, t0: float = 0.0, t1: float = 1e12) -> list[ClosedTrade]:
        rows = self._x("SELECT token,symbol,strategy,opened_ts,closed_ts,cost_usd,proceeds_usd,entry_fee_usd,"
                       "exit_fee_usd,pnl_usd,exit_reason,profile,strict_ok,data_source FROM positions "
                       "WHERE status='closed' "
                       "AND closed_ts >= ? AND closed_ts < ? ORDER BY closed_ts", (t0, t1)).fetchall()
        return [ClosedTrade(r[0], r[1], r[2], r[3], r[4], Decimal(r[5]), Decimal(r[6]),
                            Decimal(r[7]) + Decimal(r[8]), Decimal(r[9]), r[10], r[11] or "strict",
                            bool(r[12]) if r[12] is not None else True, r[13] or "gmgn") for r in rows]

    def realized_pnl_total(self) -> Decimal:
        return sum((Decimal(r[0]) for r in self._x("SELECT pnl_usd FROM positions WHERE status='closed'")), Decimal(0))

    def slippage_cost(self, t0: float = 0.0, t1: float = 1e12) -> Decimal:
        """Coût du glissement = |prix exécuté − prix moyen| × quantité, achats et ventes."""
        total = Decimal(0)
        for mid, ex, qty in self._x("SELECT mid_price, exec_price, qty FROM fills WHERE ts >= ? AND ts < ?", (t0, t1)):
            total += abs(Decimal(ex) - Decimal(mid)) * Decimal(qty)
        return total.quantize(Decimal("0.01"))

    def equity_series(self, t0: float = 0.0, t1: float = 1e12) -> list[tuple[float, Decimal]]:
        return [(r[0], Decimal(r[1]) + Decimal(r[2])) for r in
                self._x("SELECT ts, realized_usd, unrealized_usd FROM equity WHERE ts >= ? AND ts < ? ORDER BY ts",
                        (t0, t1))]

    def count(self, table: str, where: str = "1=1", args: tuple[Any, ...] = ()) -> int:
        if table not in ("decisions", "orders", "fills", "positions", "events"):
            raise ValueError(table)
        return int(self._x(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0])  # noqa: S608

    def events(self, t0: float = 0.0, t1: float = 1e12,
               kinds: tuple[str, ...] | None = None) -> list[tuple[float, str, str]]:
        rows = self._x("SELECT ts, kind, detail FROM events WHERE ts >= ? AND ts < ? ORDER BY ts", (t0, t1)).fetchall()
        return [(r[0], r[1], r[2]) for r in rows if kinds is None or r[1] in kinds]

    def failed_orders(self, t0: float = 0.0, t1: float = 1e12) -> list[tuple[float, str, str, str | None]]:
        return [(r[0], r[1], r[2], r[3]) for r in self._x(
            "SELECT ts, token, side, error FROM orders WHERE state = 'FAILED' AND ts >= ? AND ts < ? ORDER BY ts",
            (t0, t1))]
