"""Courtier PAPIER : simule l'exécution contre le dernier prix observé. Aucun appel réseau, aucun ordre réel."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from memepilot.paper.costs import CostModel

QTY = Decimal("0.000001")


class PaperRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class PaperFill:
    side: str  # buy | sell
    token: str
    mid_price_usd: Decimal
    exec_price_usd: Decimal
    qty: Decimal
    gross_usd: Decimal  # montant dépensé (achat) ou reçu avant frais (vente)
    fee_usd: Decimal
    slippage: Decimal


class PaperBroker:
    def __init__(self, costs: CostModel = CostModel()) -> None:  # noqa: B008
        self.costs = costs

    def buy(self, token: str, size_usd: Decimal, mid_price: Decimal | None,
            liquidity_usd: Decimal | None) -> PaperFill:
        if mid_price is None or mid_price <= 0:
            raise PaperRejected("prix indisponible")
        slip = self.costs.slippage(size_usd, liquidity_usd)
        if slip is None:
            raise PaperRejected("glissement trop fort ou liquidité inconnue")
        exec_price = mid_price * (1 + slip)
        qty = (size_usd / exec_price).quantize(QTY, rounding=ROUND_DOWN)
        if qty <= 0:
            raise PaperRejected("quantité nulle")
        return PaperFill("buy", token, mid_price, exec_price, qty, size_usd, self.costs.fee(size_usd), slip)

    def sell(self, token: str, qty: Decimal, mid_price: Decimal | None,
             liquidity_usd: Decimal | None) -> PaperFill:
        """Une vente réussit toujours en papier ; sans prix ou sans liquidité, elle rapporte 0 (pire cas)."""
        if mid_price is None or mid_price <= 0 or liquidity_usd is None or liquidity_usd <= 0:
            return PaperFill("sell", token, Decimal(0), Decimal(0), qty, Decimal(0), self.costs.network_fee_usd,
                             Decimal(1))
        notional = qty * mid_price
        slip = self.costs.slippage(notional, liquidity_usd) or self.costs.max_slippage
        exec_price = mid_price * (1 - slip)
        gross = (qty * exec_price).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        return PaperFill("sell", token, mid_price, exec_price, qty, gross, self.costs.fee(gross), slip)
