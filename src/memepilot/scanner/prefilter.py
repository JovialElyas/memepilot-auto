"""Préfiltre de sécurité déterministe (STRATEGY §2, idée R9 de la démo, corrigée).

Différences volontaires avec `hard_gates` de la démo :
- une donnée critique ABSENTE = REJET (la démo la traitait comme 0/False = « sûre ») ;
- seuils plus stricts (rug ≤ 0,30, top10 ≤ 0,30, bundler ≤ 0,20) ;
- autorité de gel (freeze) obligatoirement renoncée ; liquidité minimale ;
- chaque rejet porte TOUS ses motifs, pour les statistiques par filtre.

Ce module ne décide AUCUN achat : il élimine. La décision viendra en Phase 3.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from memepilot.gmgn.models import TokenInfo, TokenPool, TokenSecurity, TrenchesRow, TrendingRow


@dataclass(frozen=True)
class PrefilterConfig:
    min_liquidity_usd: float = 10_000.0
    max_rug_ratio: float = 0.30
    max_top10_holder_rate: float = 0.30
    max_dev_team_hold_rate: float = 0.10
    max_creator_hold_rate: float = 0.05
    max_bundler_rate: float = 0.20
    max_rat_trader_rate: float = 0.20
    max_tax: float = 0.10
    max_pool_liquidity_gap: float = 0.30  # écart toléré liste ↔ token pool


@dataclass
class Candidate:
    address: str
    symbol: str | None
    sources: set[str]
    liquidity: float | None
    market_cap: float | None
    price: float | None
    age_min: float | None
    rug_ratio: float | None
    renounced_mint: bool | None
    renounced_freeze: bool | None
    is_wash_trading: bool | None
    is_honeypot: bool | None
    top10: float | None
    dev_hold: float | None
    creator_hold: float | None
    bundler: float | None
    rat_trader: float | None
    buy_tax: float | None
    sell_tax: float | None
    smart_degen: int | None
    renowned: int | None
    chg_5m: float | None = None
    chg_1h: float | None = None
    buy_ratio: float | None = None
    creator: str | None = None
    # Champs utilisés par le module early token (Phase 3)
    progress: float | None = None  # avancement de la courbe de lancement (0–1)
    insider_rate: float | None = None
    fresh_wallet_rate: float | None = None
    sniper_hold: float | None = None
    bot_degen_rate: float | None = None
    creator_created_count: int | None = None
    creator_open_ratio: float | None = None
    creator_token_status: str | None = None
    enriched: bool = False
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_trending(cls, r: TrendingRow, now: float | None = None) -> Candidate:
        return cls(
            address=r.address, symbol=r.symbol, sources={"trending"}, liquidity=r.liquidity,
            market_cap=r.market_cap, price=r.price, age_min=_age_min(r.creation_timestamp or r.open_timestamp, now),
            rug_ratio=r.rug_ratio, renounced_mint=r.renounced_mint,
            renounced_freeze=r.renounced_freeze_account, is_wash_trading=r.is_wash_trading,
            is_honeypot=r.is_honeypot, top10=r.top_10_holder_rate, dev_hold=r.dev_team_hold_rate,
            creator_hold=None, bundler=r.bundler_rate, rat_trader=r.rat_trader_amount_rate,
            buy_tax=r.buy_tax, sell_tax=r.sell_tax, smart_degen=r.smart_degen_count,
            renowned=r.renowned_count, chg_5m=r.chg_5m, chg_1h=r.chg_1h, buy_ratio=r.buy_ratio,
            creator=r.creator, sniper_hold=r.top70_sniper_hold_rate, bot_degen_rate=r.bot_degen_rate,
            creator_token_status=r.creator_token_status,
        )

    @classmethod
    def from_trenches(cls, r: TrenchesRow, now: float | None = None) -> Candidate:
        return cls(
            address=r.address, symbol=r.symbol, sources={f"trenches:{r.category}"},
            liquidity=r.liquidity, market_cap=r.market_cap, price=r.price,
            age_min=_age_min(r.created_timestamp or r.open_timestamp, now), rug_ratio=r.rug_ratio,
            renounced_mint=r.renounced_mint, renounced_freeze=r.renounced_freeze_account,
            is_wash_trading=r.is_wash_trading, is_honeypot=None, top10=r.top_10_holder_rate,
            dev_hold=r.dev_team_hold_rate, creator_hold=r.creator_balance_rate,
            bundler=r.bundler_trader_amount_rate, rat_trader=r.rat_trader_amount_rate,
            buy_tax=r.buy_tax, sell_tax=r.sell_tax, smart_degen=r.smart_degen_count,
            renowned=r.renowned_count, buy_ratio=r.buy_ratio_24h, creator=r.creator,
            progress=r.progress, insider_rate=r.suspected_insider_hold_rate, fresh_wallet_rate=r.fresh_wallet_rate,
            sniper_hold=r.top70_sniper_hold_rate, bot_degen_rate=r.bot_degen_rate,
            creator_created_count=r.creator_created_count, creator_open_ratio=r.creator_created_open_ratio,
            creator_token_status=r.creator_token_status,
        )

    def merge(self, other: Candidate) -> None:
        """Même token vu par plusieurs sources : on complète les trous, sans écraser (G-10)."""
        self.sources |= other.sources
        for name in self.__dataclass_fields__:
            if name in ("sources", "warnings", "enriched", "address"):
                continue
            if getattr(self, name) is None and getattr(other, name) is not None:
                setattr(self, name, getattr(other, name))

    def apply_security(self, sec: TokenSecurity) -> None:
        """La source dédiée `token security` fait foi en cas de désaccord."""
        for attr, val in (("renounced_mint", sec.renounced_mint), ("renounced_freeze", sec.renounced_freeze_account)):
            cur = getattr(self, attr)
            if val is not None:
                if cur is not None and cur != val:
                    self.warnings.append(f"désaccord {attr} : liste={cur} sécurité={val}")
                setattr(self, attr, val)
        if sec.is_honeypot is not None:
            self.is_honeypot = sec.is_honeypot
        self.enriched = True

    def apply_token_info(self, info: TokenInfo) -> None:
        """Complète les données de momentum depuis `token info` (prix passés, achats/ventes 5 min)."""
        p = info.price_usd
        if p and info.price_5m:
            self.chg_5m = p / info.price_5m - 1
        if p and info.price_1h:
            self.chg_1h = p / info.price_1h - 1
        if info.buys_5m is not None and info.sells_5m is not None and info.buys_5m + info.sells_5m >= 10:
            self.buy_ratio = info.buys_5m / (info.buys_5m + info.sells_5m)  # 5 min : plus récent que 1 h / 24 h
        if info.liquidity is not None:
            self.liquidity = info.liquidity if self.liquidity is None else min(self.liquidity, info.liquidity)
        if info.creator_token_status and not self.creator_token_status:
            self.creator_token_status = info.creator_token_status
        self.enriched = True

    def apply_pool(self, pool: TokenPool, cfg: PrefilterConfig) -> None:
        if pool.liquidity is None:
            return
        if self.liquidity and self.liquidity > 0:
            gap = abs(pool.liquidity - self.liquidity) / self.liquidity
            if gap > cfg.max_pool_liquidity_gap:
                self.warnings.append(f"liquidité incohérente : liste={self.liquidity:.0f} pool={pool.liquidity:.0f}")
        # Prudence : on retient la plus faible des deux mesures.
        self.liquidity = pool.liquidity if self.liquidity is None else min(self.liquidity, pool.liquidity)
        self.enriched = True


def _age_min(ts: int | None, now: float | None) -> float | None:
    if not ts:
        return None
    return max(0.0, ((time.time() if now is None else now) - ts) / 60.0)


@dataclass(frozen=True)
class Verdict:
    passed: bool
    reasons: tuple[str, ...]


def _max_check(reasons: list[str], name: str, value: float | None, limit: float, required: bool) -> None:
    if value is None:
        if required:
            reasons.append(f"{name}:absent")
    elif value > limit:
        reasons.append(f"{name}:>{limit:g}")


def prefilter(c: Candidate, cfg: PrefilterConfig = PrefilterConfig()) -> Verdict:  # noqa: B008
    r: list[str] = []
    # Booléens critiques : absent = rejet
    if c.renounced_mint is not True:
        r.append("mint_non_renonce" if c.renounced_mint is False else "mint:absent")
    if c.renounced_freeze is not True:
        r.append("freeze_non_renonce" if c.renounced_freeze is False else "freeze:absent")
    if c.is_wash_trading is not False:
        r.append("wash_trading" if c.is_wash_trading else "wash_trading:absent")
    if c.is_honeypot is True:  # non fourni sur SOL : jamais considéré comme « sûr », seulement le vrai positif rejette
        r.append("honeypot")
    # Liquidité
    if c.liquidity is None:
        r.append("liquidite:absent")
    elif c.liquidity < cfg.min_liquidity_usd:
        r.append(f"liquidite:<{cfg.min_liquidity_usd:g}")
    # Taux
    _max_check(r, "rug_ratio", c.rug_ratio, cfg.max_rug_ratio, required=True)
    if c.top10 is not None and c.top10 <= 0:
        # Constaté sur données réelles : top10 = 0 exactement sur des tokens ayant des détenteurs
        # = valeur non calculée par GMGN, pas une distribution parfaite → traitée comme absente.
        r.append("top10:zero_suspect")
    else:
        _max_check(r, "top10", c.top10, cfg.max_top10_holder_rate, required=True)
    _max_check(r, "dev_hold", c.dev_hold, cfg.max_dev_team_hold_rate, required=True)
    _max_check(r, "creator_hold", c.creator_hold, cfg.max_creator_hold_rate, required=False)
    _max_check(r, "bundler", c.bundler, cfg.max_bundler_rate, required=False)
    _max_check(r, "rat_trader", c.rat_trader, cfg.max_rat_trader_rate, required=False)
    _max_check(r, "buy_tax", c.buy_tax, cfg.max_tax, required=False)
    _max_check(r, "sell_tax", c.sell_tax, cfg.max_tax, required=False)
    # Données secondaires absentes : on prévient sans rejeter (pénalité en Phase 3)
    for name, val in (("bundler", c.bundler), ("rat_trader", c.rat_trader)):
        if val is None and f"{name}:absent" not in c.warnings:
            c.warnings.append(f"{name}:absent")
    return Verdict(passed=not r, reasons=tuple(r))
