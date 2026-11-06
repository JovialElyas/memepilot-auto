"""Un cycle de scan en LECTURE SEULE (Phase 2) : collecte, validation, préfiltre, enrichissement.

Aucune décision d'achat n'est prise ici : le résultat est une liste de candidats
survivants + les motifs de rejet de tous les autres.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from memepilot.gmgn.models import (
    TokenPool,
    TokenSecurity,
    parse_trenches,
    parse_trending,
)
from memepilot.gmgn.read_client import GmgnError, GmgnRateLimited, GmgnReadClient
from memepilot.market.freshness import Fresh, StaleDataError
from memepilot.market.prices import fetch_sol_usd
from memepilot.scanner.prefilter import Candidate, PrefilterConfig, prefilter


@dataclass(frozen=True)
class ScanConfig:
    trending_interval: str = "1h"
    trending_limit: int = 50
    # D-027 : tendances 5 min en complément (repère les hausses plus tôt). Liste FACULTATIVE : si elle manque
    # (GMGN gelé — le relais n'a pas d'équivalent 5 min), le cycle continue avec la liste 1 h.
    trending_fast_interval: str | None = "5m"
    trending_fast_limit: int = 30
    trenches_types: tuple[str, ...] = ("completed", "near_completion")
    trenches_limit: int = 40
    enrich_n: int = 3  # survivants enrichis par token security + token pool
    prefilter: PrefilterConfig = field(default_factory=PrefilterConfig)


@dataclass
class ScanReport:
    started_at: float
    finished_at: float = 0.0
    sol_usd: float | None = None
    rows_seen: int = 0
    parse_errors: list[str] = field(default_factory=list)
    source_errors: list[str] = field(default_factory=list)
    rejected: int = 0
    reject_reasons: Counter[str] = field(default_factory=Counter)
    survivors: list[Candidate] = field(default_factory=list)
    calls: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    stale_sources: list[str] = field(default_factory=list)
    rate_limited: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "duration_s": round(self.finished_at - self.started_at, 2),
            "sol_usd": self.sol_usd,
            "rows_seen": self.rows_seen,
            "rejected": self.rejected,
            "reject_reasons": dict(self.reject_reasons.most_common()),
            "survivors": [
                {
                    "address": c.address, "symbol": c.symbol, "sources": sorted(c.sources),
                    "liquidity": c.liquidity, "market_cap": c.market_cap, "age_min": c.age_min,
                    "rug_ratio": c.rug_ratio, "top10": c.top10, "bundler": c.bundler,
                    "smart_degen": c.smart_degen, "renowned": c.renowned, "chg_5m": c.chg_5m,
                    "chg_1h": c.chg_1h, "buy_ratio": c.buy_ratio, "enriched": c.enriched,
                    "warnings": c.warnings,
                }
                for c in self.survivors
            ],
            "parse_errors": self.parse_errors[:20],
            "source_errors": self.source_errors,
            "stale_sources": self.stale_sources,
            "calls": self.calls,
            "avg_latency_ms": round(sum(self.latencies_ms) / len(self.latencies_ms)) if self.latencies_ms else None,
            "rate_limited": self.rate_limited,
            "decision": "AUCUNE — lecture seule (Phase 2)",
        }


async def scan_once(client: GmgnReadClient, cfg: ScanConfig = ScanConfig()) -> ScanReport:  # noqa: B008
    rep = ScanReport(started_at=time.time())
    calls_before = client.calls
    pf = cfg.prefilter

    pool: dict[str, Candidate] = {}

    def add(c: Candidate) -> None:
        if c.address in pool:
            pool[c.address].merge(c)
        else:
            pool[c.address] = c

    # 0–2. Prix SOL, trending et trenches EN PARALLÈLE (≈ 3 × 0,8 s → ≈ 0,9 s). Le régulateur de débit
    # reste unique et partagé : le parallélisme ne dépasse jamais le budget du plan.
    async def _no_list() -> None:
        return None

    sol_r, trend_r, trench_r, fast_r = await asyncio.gather(
        fetch_sol_usd(client),
        client.call(
            ("market", "trending"), chain="sol", interval=cfg.trending_interval, order_by="volume",
            direction="desc", limit=cfg.trending_limit, filter=["not_wash_trading"],
            min_liquidity=pf.min_liquidity_usd, max_top10_holder_rate=pf.max_top10_holder_rate,
        ),
        client.call(
            ("market", "trenches"), chain="sol", type=list(cfg.trenches_types), limit=cfg.trenches_limit,
            max_rug_ratio=pf.max_rug_ratio, min_liquidity=pf.min_liquidity_usd,
            max_top_holder_rate=pf.max_top10_holder_rate,
        ),
        client.call(
            ("market", "trending"), chain="sol", interval=cfg.trending_fast_interval, order_by="volume",
            direction="desc", limit=cfg.trending_fast_limit, filter=["not_wash_trading"],
            min_liquidity=pf.min_liquidity_usd, max_top10_holder_rate=pf.max_top10_holder_rate,
        ) if cfg.trending_fast_interval else _no_list(),
        return_exceptions=True,
    )
    if fast_r is not None and isinstance(fast_r, GmgnError):
        rep.source_errors.append(f"tendances {cfg.trending_fast_interval} (facultatif) : {fast_r}")
        fast_r = None

    if isinstance(sol_r, BaseException):
        if not isinstance(sol_r, (GmgnError, StaleDataError, RuntimeError)):
            raise sol_r
        rep.source_errors.append(f"prix SOL : {sol_r}")
        if isinstance(sol_r, GmgnRateLimited):
            rep.rate_limited = True
    else:
        try:
            rep.sol_usd = sol_r.get()
        except StaleDataError as exc:
            rep.source_errors.append(f"prix SOL : {exc}")

    lists: list[tuple[str, Any]] = [("trending", trend_r), ("trenches", trench_r)]
    if fast_r is not None:
        lists.append((f"trending:{cfg.trending_fast_interval}", fast_r))
    for name, res in lists:
        if isinstance(res, GmgnRateLimited):
            rep.rate_limited = True
            rep.source_errors.append(f"{name} : {res}")
            continue
        if isinstance(res, GmgnError):
            rep.source_errors.append(f"{name} : {res}")
            continue
        if isinstance(res, BaseException):
            raise res
        rep.latencies_ms.append(res.latency_ms)
        try:
            data = Fresh.of(res.data, res.fetched_at, name.split(":")[0]).get()
        except StaleDataError:
            rep.stale_sources.append(name)
            continue
        if name.startswith("trending"):
            parsed = parse_trending(data)
            rep.rows_seen += len(parsed.items) + len(parsed.errors)
            rep.parse_errors += [f"{name} {e}" for e in parsed.errors]
            for row in parsed.items:
                cand = Candidate.from_trending(row)
                cand.sources = {name}  # « trending » (1 h) ou « trending:5m »
                add(cand)
        else:
            parsed_t = parse_trenches(data)
            rep.rows_seen += len(parsed_t.items) + len(parsed_t.errors)
            rep.parse_errors += [f"trenches {e}" for e in parsed_t.errors]
            for trow in parsed_t.items:
                add(Candidate.from_trenches(trow))

    # 3. Préfiltre côté client sur TOUT (défense en profondeur).
    survivors: list[Candidate] = []
    for c in pool.values():
        v = prefilter(c, pf)
        if v.passed:
            survivors.append(c)
        else:
            rep.rejected += 1
            rep.reject_reasons.update(v.reasons)

    # 4. Enrichissement des meilleurs survivants (budget limité), puis re-filtre.
    survivors.sort(key=lambda c: ((c.smart_degen or 0) + (c.renowned or 0), c.liquidity or 0), reverse=True)
    top = survivors[: cfg.enrich_n] if not rep.rate_limited else []

    async def enrich(c: Candidate) -> None:
        sec, pl = await asyncio.gather(client.call(("token", "security"), chain="sol", address=c.address),
                                       client.call(("token", "pool"), chain="sol", address=c.address),
                                       return_exceptions=True)
        for r in (sec, pl):
            if isinstance(r, GmgnRateLimited):
                rep.rate_limited = True
                rep.source_errors.append(f"enrichissement : {r}")
            elif isinstance(r, (GmgnError, ValueError)):
                c.warnings.append(f"enrichissement échoué : {str(r)[:80]}")
            elif isinstance(r, BaseException):
                raise r
        try:
            if not isinstance(sec, BaseException) and isinstance(sec.data, dict):
                c.apply_security(TokenSecurity.from_api(sec.data))
            if not isinstance(pl, BaseException) and isinstance(pl.data, dict):
                c.apply_pool(TokenPool.model_validate(pl.data), pf)
        except ValueError as exc:
            c.warnings.append(f"enrichissement échoué : {str(exc)[:80]}")

    # 4. Enrichissement des meilleurs survivants EN PARALLÈLE (security + pool), puis re-filtre.
    await asyncio.gather(*(enrich(c) for c in top))
    final: list[Candidate] = []
    enriched_ids = {id(c) for c in top}
    for c in survivors:
        if id(c) in enriched_ids:
            v = prefilter(c, pf)
            if not v.passed:
                rep.rejected += 1
                rep.reject_reasons.update(f"apres_enrichissement:{r}" for r in v.reasons)
                continue
        final.append(c)

    rep.survivors = final
    rep.calls = client.calls - calls_before
    rep.finished_at = time.time()
    return rep
