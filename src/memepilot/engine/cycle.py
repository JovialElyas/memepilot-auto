"""Cycle d'analyse complet (Phase 3, PAPIER) : scan → flux smart money → signaux → décisions.

Budget par cycle (plan Free, poids officiels) ≈ 20 jetons :
  prix SOL 1 + trending 3 + trenches 2 + enrichissement 3×(1+1) + smartmoney 1 + kol 1 + kline 3×2.
Aucun ordre : les PROPOSITIONS sont journalisées pour l'évaluation (Phase 5).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from memepilot.config.limits import Limits
from memepilot.gmgn.models import Candle, TokenInfo, TrackedTrade, parse_kline, parse_tracked_trades
from memepilot.gmgn.read_client import GmgnError, GmgnReadClient
from memepilot.risk.guardian import BuyRequest, RiskDecision, evaluate
from memepilot.risk.state import RiskState
from memepilot.scanner.pipeline import ScanConfig, ScanReport, scan_once
from memepilot.scanner.prefilter import Candidate
from memepilot.signals.early_token import early_token_signal
from memepilot.signals.momentum import momentum_signal
from memepilot.signals.smart_money import SmartMoneyConfig, TokenFlow, build_flows, smart_money_signal
from memepilot.strategy.hybrid import Decision, HybridConfig, HybridDecision, decide


@dataclass(frozen=True)
class CycleConfig:
    scan: ScanConfig = field(default_factory=ScanConfig)
    hybrid: HybridConfig = field(default_factory=HybridConfig)
    smart_money: SmartMoneyConfig = field(default_factory=SmartMoneyConfig)
    feed_limit: int = 100
    kline_top_k: int = 3
    kline_minutes: int = 20
    info_top_k: int = 6


@dataclass
class CycleReport:
    scan: ScanReport
    decisions: list[HybridDecision] = field(default_factory=list)
    feed_ok: bool = False
    feed_trades: int = 0
    klines_fetched: int = 0
    infos_fetched: int = 0
    errors: list[str] = field(default_factory=list)
    finished_at: float = 0.0

    @property
    def proposals(self) -> list[HybridDecision]:
        return [d for d in self.decisions if d.decision is Decision.PROPOSE]

    def to_dict(self) -> dict[str, Any]:
        no_trade: dict[str, int] = {}
        for d in self.decisions:
            if d.decision is Decision.NO_TRADE:
                key = d.reasons[0].split(" — ")[0].split(" (")[0]
                no_trade[key] = no_trade.get(key, 0) + 1
        return {
            "scan": self.scan.to_dict(),
            "feed_ok": self.feed_ok, "feed_trades": self.feed_trades, "klines_fetched": self.klines_fetched,
            "errors": self.errors, "no_trade_reasons": no_trade,
            "decisions": [d.to_dict() for d in sorted(self.decisions, key=lambda d: -d.score)],
            "proposals": len(self.proposals),
            "mode": "PAPIER — aucun ordre",
        }


def _signals(c: Candidate, flows: dict[str, TokenFlow], feed_ok: bool, cfg: CycleConfig,
             candles: list[Candle] | None = None) -> list[Any]:
    return [
        momentum_signal(c, candles),
        smart_money_signal(c, flows.get(c.address), feed_ok, cfg.smart_money),
        early_token_signal(c),
    ]


@dataclass
class FeedCache:
    """Dernier flux smart money réussi : réutilisé si une requête échoue (clé de démo souvent limitée).

    La décroissance temporelle des achats (demi-vie 10 min) s'applique toujours : un flux un peu ancien
    pèse naturellement moins. Au-delà de `max_age_s`, il n'est plus utilisé.
    """

    trades: list[TrackedTrade] = field(default_factory=list)
    kol_makers: set[str] = field(default_factory=set)
    fetched_at: float = 0.0
    max_age_s: float = 600.0  # D-036 : 3 → 10 min (la demi-vie de 10 min pondère déjà l'ancienneté)

    def usable(self, now: float) -> bool:
        return bool(self.fetched_at) and now - self.fetched_at <= self.max_age_s


def _tradable(d: HybridDecision) -> bool:
    """Candidat qui PEUT devenir une proposition : pas de veto, risque ≠ HIGH (budget d'appels ciblé)."""
    return str(d.risk_class) != "HIGH" and not any(s.vetoes for s in d.signals)


async def analyze_cycle(client: GmgnReadClient, cfg: CycleConfig = CycleConfig(),  # noqa: B008
                        feed_cache: FeedCache | None = None) -> CycleReport:
    # D-036 : le flux smart money + KOL passe AVANT le scan (le quota GMGN est plein en début de cycle ;
    # après la rafale du scan, il tombait presque toujours sur un gel). Repli sur le dernier flux réussi.
    feed_errors: list[str] = []
    rep_feed_ok = False
    # Flux smart money + KOL (2 appels, poids 1 chacun) ; repli sur le dernier flux réussi récent
    trades: list[TrackedTrade] = []
    kol_makers: set[str] = set()
    try:
        sm, kol = await asyncio.gather(client.call(("track", "smartmoney"), chain="sol", limit=cfg.feed_limit),
                                       client.call(("track", "kol"), chain="sol", limit=cfg.feed_limit))
        trades += parse_tracked_trades(sm.data).items
        kol_items = parse_tracked_trades(kol.data).items
        kol_makers = {t.maker for t in kol_items}
        trades += kol_items
        rep_feed_ok = True
        if feed_cache is not None:
            feed_cache.trades, feed_cache.kol_makers, feed_cache.fetched_at = trades, kol_makers, time.time()
    except GmgnError as exc:
        feed_errors.append(f"flux smart money : {exc}")
        if feed_cache is not None and feed_cache.usable(time.time()):
            trades, kol_makers, rep_feed_ok = feed_cache.trades, feed_cache.kol_makers, True
            age = time.time() - feed_cache.fetched_at
            feed_errors.append(f"flux smart money : dernier flux réutilisé ({age:.0f} s)")
    scan = await scan_once(client, cfg.scan)
    rep = CycleReport(scan=scan)
    rep.errors += feed_errors
    rep.feed_ok = rep_feed_ok
    if scan.rate_limited:
        rep.errors.append("cycle interrompu : limitation de débit pendant le scan")
        rep.finished_at = time.time()
        return rep
    rep.feed_trades = len(trades)
    flows = build_flows(trades, time.time(), cfg.smart_money, kol_makers)
    by_addr = {c.address: c for c in scan.survivors}

    # Passe 1 : tous les survivants, données des listes seulement
    prelim = {c.address: decide(c, _signals(c, flows, rep.feed_ok, cfg), cfg.hybrid) for c in scan.survivors}

    # Passe 2 : `token info` (poids 1) pour les meilleurs candidats TRADABLES → variations 5 min / 1 h
    # et pression acheteuse 5 min (absentes des lignes trenches : cause principale de B-10).
    async def fetch_info(d: HybridDecision) -> None:
        try:
            info = await client.call(("token", "info"), chain="sol", address=d.address)
        except GmgnError as exc:
            rep.errors.append(f"token info {d.symbol or d.address[:6]} : {str(exc)[:80]}")
            return
        if isinstance(info.data, dict):
            c = by_addr[d.address]
            c.apply_token_info(TokenInfo.from_api(info.data))
            rep.infos_fetched += 1
            prelim[d.address] = decide(c, _signals(c, flows, rep.feed_ok, cfg), cfg.hybrid)

    if client.can_serve(("token", "info")):
        top = sorted((d for d in prelim.values() if _tradable(d)), key=lambda d: -d.score)[: cfg.info_top_k]
        await asyncio.gather(*(fetch_info(d) for d in top))

    # Passe 3 : bougies 1 min pour les meilleurs candidats TRADABLES (en parallèle)
    async def fetch_kline(d: HybridDecision) -> None:
        try:
            k = await client.call(("market", "kline"), chain="sol", address=d.address, resolution="1m",
                                  **{"from": int(time.time()) - cfg.kline_minutes * 60})
        except GmgnError as exc:
            rep.errors.append(f"kline {d.symbol or d.address[:6]} : {str(exc)[:80]}")
            return
        candles = parse_kline(k.data).items
        rep.klines_fetched += 1
        c = by_addr[d.address]
        prelim[d.address] = decide(c, _signals(c, flows, rep.feed_ok, cfg, candles), cfg.hybrid)

    if client.can_serve(("market", "kline")):  # rien de disponible → pas de bougies ce cycle (sans bruit)
        # Via le relais : une seule série (quota GeckoTerminal) → réservée au meilleur candidat négociable.
        k = client.kline_budget(cfg.kline_top_k)
        top = sorted((d for d in prelim.values() if _tradable(d)), key=lambda d: -d.score)[:k]
        await asyncio.gather(*(fetch_kline(d) for d in top))

    rep.decisions = list(prelim.values())
    rep.finished_at = time.time()
    return rep


def append_journal(path: Path, rep: CycleReport) -> None:
    """Journal papier append-only (une ligne JSON par décision) — matière première de la Phase 5."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = rep.finished_at
    with path.open("a", encoding="utf-8") as fh:
        for d in rep.decisions:
            if d.decision is Decision.PROPOSE or d.score >= 50:
                fh.write(json.dumps({"ts": ts, **d.to_dict()}, ensure_ascii=False) + "\n")


# ── Phase 4 : passage des propositions devant le Risk Guardian (papier, aucun ordre) ──
DEFAULT_ROUND_TRIP_FEES_USD = Decimal("0.40")  # hypothèse prudente à calibrer (Q-003)


def build_request(rep: CycleReport, d: HybridDecision, fees_usd: Decimal, wallet_usd: Decimal,
                  ledger_usd: Decimal) -> BuyRequest:
    c = next((x for x in rep.scan.survivors if x.address == d.address), None)
    liq = Decimal(str(round(c.liquidity, 2))) if c and c.liquidity is not None else None
    return BuyRequest(token=d.address, size_fraction=Decimal(str(d.size_fraction)), security_passed=c is not None,
                      data_fresh=not rep.scan.rate_limited and not rep.scan.stale_sources, liquidity_usd=liq,
                      est_round_trip_fees_usd=fees_usd, wallet_balance_usd=wallet_usd, ledger_balance_usd=ledger_usd)


def guard_proposals(rep: CycleReport, state: RiskState, limits: Limits, today: date,
                    fees_usd: Decimal = DEFAULT_ROUND_TRIP_FEES_USD) -> dict[str, RiskDecision]:
    """Verdict du Guardian pour chaque PROPOSE (sur l'état actuel). Le solde papier = capital réalisé simulé."""
    wallet = state.realized_equity_usd
    return {d.address: evaluate(build_request(rep, d, fees_usd, wallet, wallet), state, limits, today)
            for d in rep.proposals}


def dominant_module(d: HybridDecision) -> str:
    """Module qui a le plus contribué (attribution de performance par stratégie, F-42)."""
    weights = {"momentum": 0.35, "smart_money": 0.45, "early_token": 0.20}
    best = max(d.signals, key=lambda s: weights.get(s.module, 0) * s.score * s.confidence, default=None)
    return best.module if best else "inconnu"
