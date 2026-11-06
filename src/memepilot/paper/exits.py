"""Règles de sortie automatiques (STRATEGY §5), évaluées sur le dernier prix observé (toutes les ~5 s).

D-030 (2026-11-01) : plancher de gain (+8 %, frais couverts) dès +15 % atteint ; trailing resserré à −7 % au-delà de
+40 % ; moitié vendue à +50 % (une seule fois). La vente partielle est signalée par `ExitCheck.partial`.
D-026 (2026-11-01) : trailing armé dès +15 %, vente à −10 % sous le sommet (avant : +50 % / −20 %, qui laissait
les memecoins rendre tout leur gain — cas niketyson : +33 % puis −1,8 % en 40 s, sans règle entre les deux).
En réel (Phase 6), ces règles deviendront des ordres conditionnels GMGN côté serveur ; le papier reste
PESSIMISTE sur les stops (sortie au prix observé).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ExitConfig:
    stop_loss: Decimal = Decimal("0.25")
    trail_activation: Decimal = Decimal("0.15")
    trail_drawdown: Decimal = Decimal("0.10")
    time_stop_min: float = 30.0
    time_stop_min_gain: Decimal = Decimal("0.10")
    max_hold_min: float = 240.0
    liquidity_drop: Decimal = Decimal("0.40")
    max_missing_cycles: int = 5
    # D-030. Un aller-retour coûte ≈ 6,5 % sur 2,50 $ (1 % + 0,05 $ par côté) : +8 % = frais couverts + marge.
    profit_floor: Decimal | None = Decimal("0.08")  # une fois le trailing armé, jamais de sortie sous ce gain
    tight_above: Decimal = Decimal("0.40")  # au-delà de ce sommet…
    tight_drawdown: Decimal = Decimal("0.07")  # … vente à −7 % sous le sommet (au lieu de −10 %)
    partial_at: Decimal | None = Decimal("0.50")  # prise de profit partielle à +50 %…
    partial_fraction: Decimal = Decimal("0.5")  # … sur la moitié de la position
    take_profit: Decimal | None = Decimal("0.12")  # D-035 : vente TOTALE dès +12 % (prendre et partir)


# D-040 (2026-11-01) : stratégie « 5 minutes ». On ne garde JAMAIS un token plus de 5 min : vente totale à +8 %,
# stop-loss à −15 %, sinon vente à 5 min quel que soit le résultat. Les autres règles (trailing, plancher, moitié
# à +50 %, chute de liquidité) restent en secours. Rejeu sur 16 trades réels : −9,80 $ (règles D-035) → −5,08 $.
CURRENT_EXITS = ExitConfig(stop_loss=Decimal("0.15"), take_profit=Decimal("0.08"), time_stop_min=5.0,
                           max_hold_min=5.0)


@dataclass(frozen=True)
class PositionView:
    token: str
    entry_price: Decimal
    entry_liquidity: Decimal | None
    peak_price: Decimal
    opened_ts: float
    missing_cycles: int


@dataclass(frozen=True)
class ExitCheck:
    reason: str | None  # None = garder la position
    peak_price: Decimal
    missing_cycles: int
    partial: bool = False  # True : vendre `partial_fraction` de la position maintenant (D-030)


def stop_level(entry: Decimal, peak: Decimal, cfg: ExitConfig) -> Decimal | None:
    """Prix sous lequel on vend une position gagnante (None tant que le trailing n'est pas armé)."""
    peak_gain = peak / entry - 1
    if peak_gain < cfg.trail_activation:
        return None
    dd = cfg.tight_drawdown if peak_gain >= cfg.tight_above else cfg.trail_drawdown
    level = peak * (1 - dd)
    if cfg.profit_floor is not None:
        level = max(level, entry * (1 + cfg.profit_floor))
    return level


def check_exit(p: PositionView, price: Decimal | None, liquidity: Decimal | None, now: float,
               cfg: ExitConfig = ExitConfig(), partial_done: bool = True) -> ExitCheck:  # noqa: B008
    if price is None or price <= 0:
        missing = p.missing_cycles + 1
        if missing >= cfg.max_missing_cycles:
            return ExitCheck("prix indisponible (sortie au pire cas)", p.peak_price, missing)
        return ExitCheck(None, p.peak_price, missing)

    peak = max(p.peak_price, price)
    ret = price / p.entry_price - 1
    age_min = (now - p.opened_ts) / 60
    if ret <= -cfg.stop_loss:
        return ExitCheck(f"stop-loss ({ret:.0%})", peak, 0)
    if cfg.take_profit is not None and ret >= cfg.take_profit:
        return ExitCheck(f"prise de profit ({ret:+.0%})", peak, 0)
    level = stop_level(p.entry_price, peak, cfg)
    if level is not None and price <= level:
        floor = cfg.profit_floor is not None and level == p.entry_price * (1 + cfg.profit_floor)
        kind = "plancher de gain" if floor else "trailing stop"
        return ExitCheck(f"{kind} (sommet {peak / p.entry_price - 1:+.0%}, actuel {ret:+.0%})", peak, 0)
    if p.entry_liquidity and liquidity is not None and liquidity < p.entry_liquidity * (1 - cfg.liquidity_drop):
        return ExitCheck(f"chute de liquidité ({liquidity / p.entry_liquidity - 1:.0%})", peak, 0)
    if cfg.partial_at is not None and not partial_done and ret >= cfg.partial_at:
        return ExitCheck(None, peak, 0, partial=True)
    trailing_armed = peak / p.entry_price - 1 >= cfg.trail_activation
    if age_min >= cfg.time_stop_min and ret < cfg.time_stop_min_gain and not trailing_armed:
        return ExitCheck(f"time-stop ({age_min:.0f} min, {ret:+.0%})", peak, 0)
    if age_min >= cfg.max_hold_min:
        return ExitCheck(f"durée maximale ({age_min:.0f} min)", peak, 0)
    return ExitCheck(None, peak, 0)
