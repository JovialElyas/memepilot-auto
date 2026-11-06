"""Modèles Pydantic des réponses GMGN (G-07, D-011, contrat : docs/GMGN_FIELD_MAP.md).

Règle centrale : une donnée ABSENTE devient `None`, jamais 0 ni False.
(La démo GMGN convertissait l'absence en 0/False, ce qui laissait passer
un rug_ratio ou un honeypot manquant — défaut corrigé ici.)

Normalisations :
- nombres reçus en texte ("0.2485") → float ; "" / null → None ;
- booléens reçus en 0/1, "0"/"1", true/false → bool ; null → None ;
- variations de prix en pourcent (trending : 46.9 = +46.9 %) exposées aussi en fraction ;
- horodatages kline en millisecondes → secondes.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, field_validator

from memepilot.gmgn.validation import validate_sol_address


def _opt_float(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None if v is None else float(v)
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        s = v.strip()
        if s == "":
            return None
        f = float(s)  # ValueError si texte non numérique → ligne rejetée et signalée
    else:
        raise ValueError(f"nombre attendu, reçu {type(v).__name__}")
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _opt_bool(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("1", "true", "yes"):
            return True
        if s in ("0", "false", "no"):
            return False
        if s == "":
            return None
    raise ValueError(f"booléen attendu, reçu {v!r}")


def _opt_int(v: Any) -> int | None:
    f = _opt_float(v)
    return None if f is None else int(f)


def _opt_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


OptFloat = Annotated[float | None, BeforeValidator(_opt_float)]
OptBool = Annotated[bool | None, BeforeValidator(_opt_bool)]
OptInt = Annotated[int | None, BeforeValidator(_opt_int)]
OptStr = Annotated[str | None, BeforeValidator(_opt_str)]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


class _HasAddress(_Model):
    address: str

    @field_validator("address")
    @classmethod
    def _addr(cls, v: str) -> str:
        return validate_sol_address(v)


def _sub(d: dict[str, Any], key: str) -> dict[str, Any]:
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def _pct_to_fraction(v: float | None) -> float | None:
    return None if v is None else v / 100.0


# ───────────────────────────── market trending ─────────────────────────────
class TrendingRow(_HasAddress):
    symbol: OptStr = None
    name: OptStr = None
    price: OptFloat = None
    price_change_percent1m: OptFloat = None
    price_change_percent5m: OptFloat = None
    price_change_percent1h: OptFloat = None
    volume: OptFloat = None
    liquidity: OptFloat = None
    initial_liquidity: OptFloat = None
    market_cap: OptFloat = None
    swaps: OptInt = None
    buys: OptInt = None
    sells: OptInt = None
    holder_count: OptInt = None
    creation_timestamp: OptInt = None
    open_timestamp: OptInt = None
    launchpad_platform: OptStr = None
    # sécurité
    renounced_mint: OptBool = None
    renounced_freeze_account: OptBool = None
    is_honeypot: OptBool = None
    is_wash_trading: OptBool = None
    rug_ratio: OptFloat = None
    buy_tax: OptFloat = None
    sell_tax: OptFloat = None
    # structure des détenteurs / flux
    top_10_holder_rate: OptFloat = None
    dev_team_hold_rate: OptFloat = None
    bundler_rate: OptFloat = None
    rat_trader_amount_rate: OptFloat = None
    entrapment_ratio: OptFloat = None
    top70_sniper_hold_rate: OptFloat = None
    bot_degen_rate: OptFloat = None
    sniper_count: OptInt = None
    # consensus
    smart_degen_count: OptInt = None
    renowned_count: OptInt = None
    # créateur
    creator: OptStr = None
    creator_token_status: OptStr = None

    @property
    def chg_1m(self) -> float | None:
        return _pct_to_fraction(self.price_change_percent1m)

    @property
    def chg_5m(self) -> float | None:
        return _pct_to_fraction(self.price_change_percent5m)

    @property
    def chg_1h(self) -> float | None:
        return _pct_to_fraction(self.price_change_percent1h)

    @property
    def buy_ratio(self) -> float | None:
        if self.buys is None or self.sells is None or self.buys + self.sells == 0:
            return None
        return self.buys / (self.buys + self.sells)


# ───────────────────────────── market trenches ─────────────────────────────
class TrenchesRow(_HasAddress):
    category: str = "unknown"  # new_creation | near_completion | completed (ajouté au parsing)
    symbol: OptStr = None
    name: OptStr = None
    price: OptFloat = None
    liquidity: OptFloat = None
    market_cap: OptFloat = None
    progress: OptFloat = None
    status: OptInt = None
    created_timestamp: OptInt = None
    open_timestamp: OptInt = None
    complete_timestamp: OptInt = None
    launchpad_platform: OptStr = None
    holder_count: OptInt = None
    volume_24h: OptFloat = None
    swaps_24h: OptInt = None
    buys_24h: OptInt = None
    sells_24h: OptInt = None
    net_buy_24h: OptFloat = None
    # sécurité
    renounced_mint: OptBool = None
    renounced_freeze_account: OptBool = None
    is_wash_trading: OptBool = None
    rug_ratio: OptFloat = None
    buy_tax: OptFloat = None
    sell_tax: OptFloat = None
    # structure
    top_10_holder_rate: OptFloat = None
    dev_team_hold_rate: OptFloat = None
    creator_balance_rate: OptFloat = None
    bundler_trader_amount_rate: OptFloat = None
    rat_trader_amount_rate: OptFloat = None
    suspected_insider_hold_rate: OptFloat = None
    fresh_wallet_rate: OptFloat = None
    top70_sniper_hold_rate: OptFloat = None
    entrapment_ratio: OptFloat = None
    bot_degen_rate: OptFloat = None
    # consensus
    smart_degen_count: OptInt = None
    renowned_count: OptInt = None
    # créateur
    creator: OptStr = None
    creator_token_status: OptStr = None
    creator_created_count: OptInt = None
    creator_created_open_count: OptInt = None
    creator_created_inner_count: OptInt = None
    creator_created_open_ratio: OptFloat = None

    @property
    def buy_ratio_24h(self) -> float | None:
        if self.buys_24h is None or self.sells_24h is None or self.buys_24h + self.sells_24h == 0:
            return None
        return self.buys_24h / (self.buys_24h + self.sells_24h)


# ───────────────────────────── token security / pool / info ─────────────────────────────
class TokenSecurity(_HasAddress):
    renounced_mint: OptBool = None
    renounced_freeze_account: OptBool = None
    is_honeypot: OptBool = None  # non fourni sur Solana (null) → ne JAMAIS l'interpréter comme « sûr »
    top_10_holder_rate: OptFloat = None
    buy_tax: OptFloat = None
    sell_tax: OptFloat = None
    burn_status: OptStr = None
    lock_percent: OptFloat = None

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> TokenSecurity:
        lock = d.get("lock_summary") or {}
        return cls.model_validate({**d, "lock_percent": lock.get("lock_percent")})


class TokenPool(_HasAddress):
    pool_address: OptStr = None
    quote_symbol: OptStr = None
    liquidity: OptFloat = None
    initial_liquidity: OptFloat = None
    base_reserve: OptFloat = None
    quote_reserve: OptFloat = None
    creation_timestamp: OptInt = None
    exchange: OptStr = None


class TokenInfo(_HasAddress):
    symbol: OptStr = None
    name: OptStr = None
    decimals: OptInt = None
    liquidity: OptFloat = None
    holder_count: OptInt = None
    circulating_supply: OptFloat = None
    creation_timestamp: OptInt = None
    open_timestamp: OptInt = None
    launchpad_platform: OptStr = None
    # aplatis depuis price.*, dev.*, stat.*, wallet_tags_stat.*
    price_usd: OptFloat = None
    price_1m: OptFloat = None
    price_5m: OptFloat = None
    price_1h: OptFloat = None
    volume_5m: OptFloat = None
    volume_1h: OptFloat = None
    buys_5m: OptInt = None
    sells_5m: OptInt = None
    creator_address: OptStr = None
    creator_token_status: OptStr = None
    top_10_holder_rate: OptFloat = None
    dev_team_hold_rate: OptFloat = None
    creator_hold_rate: OptFloat = None
    top_bundler_trader_percentage: OptFloat = None
    top_rat_trader_percentage: OptFloat = None
    fresh_wallet_rate: OptFloat = None
    top70_sniper_hold_rate: OptFloat = None
    creator_created_count: OptInt = None
    smart_wallets: OptInt = None
    renowned_wallets: OptInt = None
    sniper_wallets: OptInt = None

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> TokenInfo:
        price: dict[str, Any] = _sub(d, "price")
        dev = _sub(d, "dev")
        stat = _sub(d, "stat")
        tags = _sub(d, "wallet_tags_stat")
        flat = {
            **d,
            "price_usd": price.get("price"),
            "price_1m": price.get("price_1m"),
            "price_5m": price.get("price_5m"),
            "price_1h": price.get("price_1h"),
            "volume_5m": price.get("volume_5m"),
            "volume_1h": price.get("volume_1h"),
            "buys_5m": price.get("buys_5m"),
            "sells_5m": price.get("sells_5m"),
            "creator_address": dev.get("creator_address"),
            "creator_token_status": dev.get("creator_token_status"),
            "top_10_holder_rate": stat.get("top_10_holder_rate", dev.get("top_10_holder_rate")),
            "dev_team_hold_rate": stat.get("dev_team_hold_rate"),
            "creator_hold_rate": stat.get("creator_hold_rate"),
            "top_bundler_trader_percentage": stat.get("top_bundler_trader_percentage"),
            "top_rat_trader_percentage": stat.get("top_rat_trader_percentage"),
            "fresh_wallet_rate": stat.get("fresh_wallet_rate"),
            "top70_sniper_hold_rate": stat.get("top70_sniper_hold_rate"),
            "creator_created_count": stat.get("creator_created_count"),
            "smart_wallets": tags.get("smart_wallets"),
            "renowned_wallets": tags.get("renowned_wallets"),
            "sniper_wallets": tags.get("sniper_wallets"),
        }
        flat.pop("price", None)
        return cls.model_validate(flat)

    @property
    def is_unknown(self) -> bool:
        """gmgn-cli renvoie un succès avec des champs vides pour un token inexistant.

        ⚠ Constaté le 2026-10-31 : un token inexistant reçoit quand même un PRIX non nul
        (0.000131 $). Le prix ne prouve donc pas l'existence : on se fie au symbole et aux décimales.
        """
        return not self.symbol and not self.decimals


# ───────────────────────────── kline / holders / trades / wallets ─────────────────────────────
class Candle(_Model):
    time: int  # secondes
    open: float
    high: float
    low: float
    close: float
    volume: OptFloat = None

    @field_validator("time", mode="before")
    @classmethod
    def _ms_to_s(cls, v: Any) -> int:
        t = int(float(v))
        return t // 1000 if t > 10**11 else t

    @field_validator("open", "high", "low", "close", mode="before")
    @classmethod
    def _price(cls, v: Any) -> float:
        f = _opt_float(v)
        if f is None or f <= 0:
            raise ValueError("prix de bougie absent ou ≤ 0")
        return f


class Holder(_HasAddress):
    amount_percentage: OptFloat = None
    usd_value: OptFloat = None
    is_suspicious: OptBool = None
    is_new: OptBool = None
    wallet_tag_v2: OptStr = None
    tags: list[str] = Field(default_factory=list)
    start_holding_at: OptInt = None
    exchange: OptStr = None


class TrackedTrade(_Model):
    """Ligne de `track smartmoney` / `track kol`."""

    transaction_hash: str
    maker: str
    token: str = Field(alias="base_address")
    side: str
    amount_usd: OptFloat = None
    price_usd: OptFloat = None
    timestamp: int
    is_open_or_close: OptInt = None
    token_symbol: OptStr = None
    maker_tags: list[str] = Field(default_factory=list)

    @field_validator("maker", "token")
    @classmethod
    def _addr(cls, v: str) -> str:
        return validate_sol_address(v)

    @field_validator("side")
    @classmethod
    def _side(cls, v: str) -> str:
        if v not in ("buy", "sell"):
            raise ValueError(f"side inattendu : {v!r}")
        return v

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> TrackedTrade:
        bt = d.get("base_token") or {}
        mi = d.get("maker_info") or {}
        return cls.model_validate({**d, "token_symbol": bt.get("symbol"), "maker_tags": mi.get("tags") or []})


class BoundWallet(_Model):
    chain: str
    address: str


# ───────────────────────────── parsing avec rapport d'erreurs ─────────────────────────────
M = TypeVar("M", bound=BaseModel)


@dataclass
class ParseReport(Generic[M]):  # noqa: UP046 — compatibilité Python 3.11
    items: list[M] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_many(rows: Iterable[Any], build: Callable[[dict[str, Any]], M]) -> ParseReport[M]:
    """Valide chaque ligne ; une ligne invalide est écartée et signalée, jamais corrigée en silence."""
    rep: ParseReport[M] = ParseReport()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            rep.errors.append(f"ligne {i}: objet attendu")
            continue
        try:
            rep.items.append(build(row))
        except (ValidationError, ValueError) as exc:
            addr = row.get("address") or row.get("base_address") or "?"
            first = str(exc).splitlines()[0][:160]
            rep.errors.append(f"ligne {i} ({addr}): {first}")
    return rep


def _list_of(data: Any, *keys: str) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def parse_trending(data: Any) -> ParseReport[TrendingRow]:
    return parse_many(_list_of(data, "rank", "tokens"), TrendingRow.model_validate)


def _trenches_builder(cat: str) -> Callable[[dict[str, Any]], TrenchesRow]:
    def build(r: dict[str, Any]) -> TrenchesRow:
        return TrenchesRow.model_validate({**r, "category": cat})

    return build


def parse_trenches(data: Any) -> ParseReport[TrenchesRow]:
    rep: ParseReport[TrenchesRow] = ParseReport()
    if not isinstance(data, dict):
        rep.errors.append("trenches : objet attendu")
        return rep
    for cat in ("new_creation", "near_completion", "completed"):
        sub = parse_many(data.get(cat) or [], _trenches_builder(cat))
        rep.items += sub.items
        rep.errors += [f"{cat} {e}" for e in sub.errors]
    return rep


def parse_kline(data: Any) -> ParseReport[Candle]:
    rep = parse_many(_list_of(data, "list"), Candle.model_validate)
    rep.items.sort(key=lambda c: c.time)
    return rep


def parse_holders(data: Any) -> ParseReport[Holder]:
    return parse_many(_list_of(data, "list"), Holder.model_validate)


def parse_tracked_trades(data: Any) -> ParseReport[TrackedTrade]:
    return parse_many(_list_of(data, "list"), TrackedTrade.from_api)


def parse_bound_wallets(data: Any) -> ParseReport[BoundWallet]:
    return parse_many(_list_of(data, "wallets"), BoundWallet.model_validate)
