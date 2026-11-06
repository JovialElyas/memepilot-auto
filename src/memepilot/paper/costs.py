"""Modèle de coûts conservateur pour le trading papier (STRATEGY §6, Q-003).

Hypothèses (à recalibrer avec des frais réels en Phase 6) :
- frais de plateforme : 1 % du montant échangé, à l'achat comme à la vente ;
- frais réseau (priority fee + tip) : 0,05 $ par transaction ;
- glissement de prix : 0,5 % de base + impact AMM ≈ 2 × taille / liquidité ;
- au-delà de 30 % de glissement (plafond de `limits.yaml`), l'ordre serait rejeté.
Ces valeurs sont volontairement pessimistes : un résultat papier positif doit survivre à la réalité.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

CENT = Decimal("0.01")


@dataclass(frozen=True)
class CostModel:
    platform_fee_rate: Decimal = Decimal("0.01")
    network_fee_usd: Decimal = Decimal("0.05")
    base_slippage: Decimal = Decimal("0.005")
    impact_factor: Decimal = Decimal("2")
    max_slippage: Decimal = Decimal("0.30")
    guardian_fee_floor_usd: Decimal = Decimal("0.40")
    guardian_safety: Decimal = Decimal("1.5")

    def fee(self, notional_usd: Decimal) -> Decimal:
        return (max(Decimal(0), notional_usd) * self.platform_fee_rate + self.network_fee_usd).quantize(CENT)

    def slippage(self, size_usd: Decimal, liquidity_usd: Decimal | None) -> Decimal | None:
        """Glissement attendu (fraction) ; None = ordre impossible (liquidité inconnue ou glissement trop fort)."""
        if liquidity_usd is None or liquidity_usd <= 0:
            return None
        s = self.base_slippage + self.impact_factor * size_usd / liquidity_usd
        return None if s > self.max_slippage else s

    def guardian_fee_estimate(self, max_position_usd: Decimal) -> Decimal:
        """Frais aller-retour présentés au Guardian : jamais sous le plancher, avec marge de sécurité.

        Garantit que les frais réels (entrée + sortie à perte) ne dépassent pas l'estimation,
        hypothèse sur laquelle reposent les tests de propriétés du Guardian.
        """
        est = (self.fee(max_position_usd) * 2 * self.guardian_safety).quantize(CENT)
        return max(self.guardian_fee_floor_usd, est)
