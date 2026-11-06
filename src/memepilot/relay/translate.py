"""Traduction des réponses DexScreener / GeckoTerminal / RugCheck vers le FORMAT GMGN (D-024).

Fonctions pures : aucune requête réseau ici. Le reste du bot (modèles Pydantic, préfiltre, signaux,
Guardian, interface) reçoit exactement les mêmes structures qu'avec GMGN et les revalide lui-même.

Règle de prudence : une donnée qu'aucune source ne fournit reste ABSENTE (None), jamais 0 / False,
afin que le préfiltre rejette le token plutôt que de le croire sûr.
Deux champs GMGN n'ont pas d'équivalent exact et sont APPROCHÉS (documentés dans D-024) :
- rug_ratio : 1.0 si RugCheck marque le token « rugged » ou signale un risque de niveau « danger », sinon 0.0 ;
- is_wash_trading : vrai si moins de 15 % de wallets distincts pour ≥ 50 transactions sur 1 h (peu de wallets
  qui tournent en boucle), faux sinon ; absent si GeckoTerminal ne fournit pas les transactions.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

SOL_MINT = "So11111111111111111111111111111111111111112"
QUOTE_MINTS = frozenset({
    SOL_MINT,
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
})
MIN_PAIR_LIQ_USD = 1_000.0  # pair sans liquidité réelle → son prix n'est pas fiable (cas NIKETYSON, 2026-11-01)
WASH_MIN_TRADES = 50
WASH_MAX_UNIQUE_RATIO = 0.15


def _f(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    f = _f(v)
    return None if f is None else int(f)


def _epoch(iso: Any) -> int | None:
    if not isinstance(iso, str):
        return None
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _past_price(price: float | None, pct: float | None) -> float | None:
    """Prix d'il y a N minutes à partir du prix actuel et de la variation en %."""
    if price is None or pct is None or pct <= -100:
        return None
    return price / (1 + pct / 100)


# ───────────────────────────── RugCheck → sécurité ─────────────────────────────
def rug_security(report: dict[str, Any]) -> dict[str, Any]:
    """Champs de sécurité au format GMGN depuis un rapport RugCheck complet (/v1/tokens/{mint}/report)."""
    raw_token = report.get("token")
    token: dict[str, Any] = raw_token if isinstance(raw_token, dict) else {}
    supply = _f(token.get("supply"))

    def renounced(key: str) -> bool | None:
        if key in report:
            return report[key] is None
        if key in token:
            return token[key] is None
        return None

    # Top 10 hors pools de liquidité / lockers connus (sinon le pool lui-même compte comme « baleine »)
    excluded = {m.get("pubkey") for m in report.get("markets") or [] if isinstance(m, dict)}
    for addr, info in (report.get("knownAccounts") or {}).items():
        if isinstance(info, dict) and info.get("type") in ("AMM", "LOCKER"):
            excluded.add(addr)
    holders = [h for h in report.get("topHolders") or [] if isinstance(h, dict)]
    top10: float | None = None
    if holders:
        kept = [h for h in holders if h.get("owner") not in excluded and h.get("address") not in excluded]
        top10 = sum(_f(h.get("pct")) or 0.0 for h in kept[:10]) / 100

    creator_balance = _f(report.get("creatorBalance"))
    dev_hold = creator_balance / supply if creator_balance is not None and supply else None
    insider_amt = sum(_f(n.get("currentHolding")) or 0.0 for n in report.get("insiderNetworks") or []
                      if isinstance(n, dict))
    insider = insider_amt / supply if supply else None

    risks = [r for r in report.get("risks") or [] if isinstance(r, dict)]
    danger = report.get("rugged") is True or any(str(r.get("level", "")).lower() == "danger" for r in risks)
    rug_ratio = 1.0 if danger else (0.0 if "rugged" in report or "risks" in report else None)

    return {
        "renounced_mint": renounced("mintAuthority"),
        "renounced_freeze_account": renounced("freezeAuthority"),
        "top_10_holder_rate": top10,
        "dev_team_hold_rate": dev_hold,
        "creator_balance_rate": dev_hold,
        "suspected_insider_hold_rate": insider,
        "rug_ratio": rug_ratio,
        "holder_count": _i(report.get("totalHolders")),
        "creator": report.get("creator") if isinstance(report.get("creator"), str) else None,
        "lock_percent": _f(report.get("lpLockedPct")),
        "is_honeypot": None,  # ni GMGN ni RugCheck ne le fournissent sur Solana : jamais « sûr » par défaut
        "rug_risks": [str(r.get("name")) for r in risks][:6],
    }


def security_response(address: str, report: dict[str, Any]) -> dict[str, Any]:
    """Réponse au format `gmgn-cli token security`."""
    s = rug_security(report)
    return {"address": address, "renounced_mint": s["renounced_mint"],
            "renounced_freeze_account": s["renounced_freeze_account"], "is_honeypot": None,
            "top_10_holder_rate": s["top_10_holder_rate"], "lock_summary": {"lock_percent": s["lock_percent"]}}


# ───────────────────────────── GeckoTerminal → listes ─────────────────────────────
def _wash(tx: dict[str, Any]) -> bool | None:
    buys, sells = _i(tx.get("buys")), _i(tx.get("sells"))
    buyers, sellers = _i(tx.get("buyers")), _i(tx.get("sellers"))
    if buys is None or sells is None:
        return None
    trades = buys + sells
    if trades < WASH_MIN_TRADES or buyers is None or sellers is None:
        return False
    return (buyers + sellers) / trades < WASH_MAX_UNIQUE_RATIO


def gecko_pools(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Pools GeckoTerminal (trending_pools / new_pools, avec include=base_token) → lignes neutres."""
    tokens = {t.get("id"): t.get("attributes") or {} for t in payload.get("included") or [] if isinstance(t, dict)}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in payload.get("data") or []:
        if not isinstance(p, dict):
            continue
        a = p.get("attributes") or {}
        rel = p.get("relationships") or {}
        base_id = ((rel.get("base_token") or {}).get("data") or {}).get("id") or ""
        address = base_id.split("_", 1)[1] if "_" in base_id else ""
        if not address or address in QUOTE_MINTS or address in seen:
            continue
        seen.add(address)
        tok = tokens.get(base_id, {})
        chg = a.get("price_change_percentage") or {}
        tx = a.get("transactions") or {}
        vol = a.get("volume_usd") or {}
        h1, h24, m5 = tx.get("h1") or {}, tx.get("h24") or {}, tx.get("m5") or {}
        rows.append({
            "address": address, "symbol": tok.get("symbol"), "name": tok.get("name"),
            "decimals": _i(tok.get("decimals")), "pool": a.get("address"),
            "dex": ((rel.get("dex") or {}).get("data") or {}).get("id"),
            "price": _f(a.get("base_token_price_usd")), "liquidity": _f(a.get("reserve_in_usd")),
            "market_cap": _f(a.get("market_cap_usd")) or _f(a.get("fdv_usd")),
            "chg_m5": _f(chg.get("m5")), "chg_h1": _f(chg.get("h1")),
            "volume_h1": _f(vol.get("h1")), "volume_h24": _f(vol.get("h24")),
            "buys_h1": _i(h1.get("buys")), "sells_h1": _i(h1.get("sells")),
            "buys_h24": _i(h24.get("buys")), "sells_h24": _i(h24.get("sells")),
            "buys_m5": _i(m5.get("buys")), "sells_m5": _i(m5.get("sells")),
            "wash": _wash(h1 if h1 else h24), "created": _epoch(a.get("pool_created_at")),
        })
    return rows


def trending_row(r: dict[str, Any], sec: dict[str, Any] | None) -> dict[str, Any]:
    """Ligne au format `gmgn-cli market trending` (rank[])."""
    s = sec or {}
    buys, sells = r.get("buys_h1"), r.get("sells_h1")
    return {
        "address": r["address"], "symbol": r.get("symbol"), "name": r.get("name"), "price": r.get("price"),
        "price_change_percent5m": r.get("chg_m5"), "price_change_percent1h": r.get("chg_h1"),
        "volume": r.get("volume_h1"), "liquidity": r.get("liquidity"), "market_cap": r.get("market_cap"),
        "buys": buys, "sells": sells, "swaps": None if buys is None or sells is None else buys + sells,
        "holder_count": s.get("holder_count"), "creation_timestamp": r.get("created"),
        "launchpad_platform": r.get("dex"), "is_wash_trading": r.get("wash"),
        "renounced_mint": s.get("renounced_mint"), "renounced_freeze_account": s.get("renounced_freeze_account"),
        "rug_ratio": s.get("rug_ratio"), "top_10_holder_rate": s.get("top_10_holder_rate"),
        "dev_team_hold_rate": s.get("dev_team_hold_rate"), "creator": s.get("creator"),
        "is_honeypot": None,
    }


def trenches_response(rows: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> dict[str, Any]:
    """Réponse au format `gmgn-cli market trenches` : pump.fun encore sur la courbe = new_creation."""
    out: dict[str, list[dict[str, Any]]] = {"new_creation": [], "near_completion": [], "completed": []}
    for r, sec in rows:
        s = sec or {}
        cat = "new_creation" if r.get("dex") in ("pump-fun", "pumpfun") else "completed"
        out[cat].append({
            "address": r["address"], "symbol": r.get("symbol"), "name": r.get("name"), "price": r.get("price"),
            "liquidity": r.get("liquidity"), "market_cap": r.get("market_cap"),
            "created_timestamp": r.get("created"), "open_timestamp": r.get("created"),
            "launchpad_platform": r.get("dex"), "holder_count": s.get("holder_count"),
            "volume_24h": r.get("volume_h24"), "buys_24h": r.get("buys_h24"), "sells_24h": r.get("sells_h24"),
            "is_wash_trading": r.get("wash"), "renounced_mint": s.get("renounced_mint"),
            "renounced_freeze_account": s.get("renounced_freeze_account"), "rug_ratio": s.get("rug_ratio"),
            "top_10_holder_rate": s.get("top_10_holder_rate"), "dev_team_hold_rate": s.get("dev_team_hold_rate"),
            "creator_balance_rate": s.get("creator_balance_rate"),
            "suspected_insider_hold_rate": s.get("suspected_insider_hold_rate"), "creator": s.get("creator"),
        })
    return out


# ───────────────────────────── DexScreener → token info / pool ─────────────────────────────
def best_pair(pairs: Any, address: str) -> dict[str, Any] | None:
    """Pair la plus liquide où le token est l'actif de base (prix du token, pas de la contrepartie).

    Les pairs sans liquidité mesurée (≥ 1 000 $) ou sans prix positif sont ignorées : leur prix peut être faux
    de plusieurs ordres de grandeur. Aucune pair fiable → None → « prix indisponible », jamais un faux prix.
    """
    if not isinstance(pairs, list):
        return None
    cands = [p for p in pairs if isinstance(p, dict) and (p.get("baseToken") or {}).get("address") == address
             and (_f((p.get("liquidity") or {}).get("usd")) or 0.0) >= MIN_PAIR_LIQ_USD
             and (_f(p.get("priceUsd")) or 0.0) > 0]
    if not cands:
        return None
    return max(cands, key=lambda p: _f((p.get("liquidity") or {}).get("usd")) or 0.0)


def token_info_response(address: str, pair: dict[str, Any] | None) -> dict[str, Any]:
    """Réponse au format `gmgn-cli token info`. Pair absente → objet vide (token inconnu)."""
    if pair is None:
        return {"address": address}
    base = pair.get("baseToken") or {}
    price = _f(pair.get("priceUsd"))
    chg = pair.get("priceChange") or {}
    m5 = (pair.get("txns") or {}).get("m5") or {}
    vol = pair.get("volume") or {}
    created = _i(pair.get("pairCreatedAt"))
    return {
        "address": address, "symbol": base.get("symbol"), "name": base.get("name"),
        "liquidity": _f((pair.get("liquidity") or {}).get("usd")),
        "creation_timestamp": None if created is None else created // 1000,
        "price": {"price": price, "price_5m": _past_price(price, _f(chg.get("m5"))),
                  "price_1h": _past_price(price, _f(chg.get("h1"))), "buys_5m": _i(m5.get("buys")),
                  "sells_5m": _i(m5.get("sells")), "volume_5m": _f(vol.get("m5")), "volume_1h": _f(vol.get("h1"))},
    }


def pool_response(address: str, pair: dict[str, Any] | None) -> dict[str, Any]:
    """Réponse au format `gmgn-cli token pool`."""
    if pair is None:
        return {"address": address}
    created = _i(pair.get("pairCreatedAt"))
    return {"address": address, "pool_address": pair.get("pairAddress"), "exchange": pair.get("dexId"),
            "liquidity": _f((pair.get("liquidity") or {}).get("usd")),
            "quote_symbol": (pair.get("quoteToken") or {}).get("symbol"),
            "creation_timestamp": None if created is None else created // 1000}


# ───────────────────────────── GeckoTerminal → bougies ─────────────────────────────
def kline_response(payload: dict[str, Any], since: float | None = None) -> dict[str, Any]:
    """Réponse au format `gmgn-cli market kline` ({list: [...]}) depuis l'OHLCV GeckoTerminal."""
    rows = ((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    since = since if since is not None else time.time() - 3600
    out = []
    for r in rows:
        if isinstance(r, list) and len(r) >= 5 and (_f(r[0]) or 0) >= since:
            out.append({"time": int(r[0]), "open": r[1], "high": r[2], "low": r[3], "close": r[4],
                        "volume": r[5] if len(r) > 5 else None})
    return {"list": out}
