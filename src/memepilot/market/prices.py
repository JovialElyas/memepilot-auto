"""Prix SOL/USD et conversions USD ↔ lamports (G-09).

Les limites sont en USD (position 5 $), les ordres en lamports (1 SOL = 10⁹).
Source : `token info` sur le mint SOL (champ price.price). Contrôles :
bornes de vraisemblance + cohérence avec le prix d'il y a 1 minute.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from memepilot.gmgn.models import TokenInfo
from memepilot.gmgn.read_client import GmgnReadClient
from memepilot.market.freshness import Fresh

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 10**9
MIN_PLAUSIBLE_USD = 1.0
MAX_PLAUSIBLE_USD = 10_000.0
MAX_1M_DEVIATION = 0.05


class PriceError(RuntimeError):
    pass


def validate_sol_price(info: TokenInfo) -> float:
    if info.address != SOL_MINT:
        raise PriceError("ce n'est pas le mint SOL")
    p = info.price_usd
    if p is None:
        raise PriceError("prix SOL absent")
    if not MIN_PLAUSIBLE_USD <= p <= MAX_PLAUSIBLE_USD:
        raise PriceError(f"prix SOL invraisemblable : {p}")
    if info.price_1m is not None and info.price_1m > 0:
        dev = abs(p / info.price_1m - 1)
        if dev > MAX_1M_DEVIATION:
            raise PriceError(f"prix SOL incohérent : écart {dev:.1%} avec le prix d'il y a 1 min")
    return p


async def fetch_sol_usd(client: GmgnReadClient) -> Fresh[float]:
    # Cache court (20 s < TTL de fraîcheur 30 s) : une requête de moins par cycle sur une clé saturée.
    res = await client.call(("token", "info"), chain="sol", address=SOL_MINT, cache_ttl_s=20)
    if not isinstance(res.data, dict):
        raise PriceError("réponse token info inattendue")
    price = validate_sol_price(TokenInfo.from_api(res.data))
    return Fresh.of(price, res.fetched_at, "sol_price")


def usd_to_lamports(usd: Decimal, sol_usd: float) -> int:
    """Arrondi vers le bas : on ne dépense jamais plus que le montant USD demandé."""
    if usd <= 0:
        raise ValueError("montant USD ≤ 0")
    if sol_usd <= 0:
        raise ValueError("prix SOL ≤ 0")
    sol = (usd / Decimal(str(sol_usd))).quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)
    return int(sol * LAMPORTS_PER_SOL)


def lamports_to_usd(lamports: int, sol_usd: float) -> Decimal:
    return (Decimal(lamports) / LAMPORTS_PER_SOL * Decimal(str(sol_usd))).quantize(Decimal("0.0001"))
