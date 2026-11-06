"""Risk Guardian (SPEC F-20 à F-24, D-010) — VETO absolu, fonction pure.

Aucun réseau, aucune base de données, aucun LLM : uniquement (demande, état, limites) → décision.
Toutes les règles sont évaluées et TOUS les motifs de refus sont renvoyés (audit).

Taille d'une nouvelle position (D-010) :
    perte_max_nouvelle = taille + frais_aller_retour_estimés
    pertes_jour     + exposition_ouverte + perte_max_nouvelle ≤ limite_jour
    pertes_cumulées + exposition_ouverte + perte_max_nouvelle ≤ limite_cumulée
    taille = min(position_max × fraction, budgets − frais, solde − frais), arrondie au cent inférieur
    taille < position_min → refus (frais trop lourds)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal

from memepilot.config.limits import Limits
from memepilot.risk.state import ZERO, RiskState

CENT = Decimal("0.01")


@dataclass(frozen=True)
class GuardianConfig:
    min_liquidity_usd: Decimal = Decimal("10000")
    max_wallet_ledger_gap_usd: Decimal = Decimal("1.00")  # écart toléré solde réel ↔ ledger


@dataclass(frozen=True)
class BuyRequest:
    token: str
    size_fraction: Decimal  # proposée par la stratégie (1 / 0,5) ; jamais > 1
    security_passed: bool
    data_fresh: bool
    liquidity_usd: Decimal | None
    est_round_trip_fees_usd: Decimal | None
    wallet_balance_usd: Decimal | None  # solde réel du wallet (papier : capital simulé)
    ledger_balance_usd: Decimal | None  # solde attendu selon le ledger


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    size_usd: Decimal
    reasons: tuple[str, ...]
    budget_day_usd: Decimal
    budget_cumulative_usd: Decimal

    def to_dict(self) -> dict[str, object]:
        return {"approved": self.approved, "size_usd": str(self.size_usd), "reasons": list(self.reasons),
                "budget_day_usd": str(self.budget_day_usd), "budget_cumulative_usd": str(self.budget_cumulative_usd)}


def budgets(state: RiskState, limits: Limits) -> tuple[Decimal, Decimal]:
    """Budget de perte encore disponible (jour, cumul), positions ouvertes incluses au pire cas."""
    day = limits.daily_loss_limit_usd - state.daily_loss_usd - state.open_exposure_usd
    cum = limits.maximum_cumulative_loss_usd - state.cumulative_loss_usd - state.open_exposure_usd
    return day, cum


def evaluate(req: BuyRequest, state: RiskState, limits: Limits, today: date,
             cfg: GuardianConfig = GuardianConfig()) -> RiskDecision:  # noqa: B008
    r: list[str] = []
    b_day, b_cum = budgets(state, limits)

    # 1. Verrous et intégrité
    if state.locked:
        r.append(f"LOCK actif ({state.lock_reason or 'perte cumulée'})")
    if state.halted:
        r.append("HALT actif (arrêt d'urgence)")
    if state.limits_fingerprint != limits.fingerprint():
        r.append("limites modifiées depuis l'initialisation de l'état")
    if state.day != today:
        r.append("état non basculé au jour courant")

    # 2. Qualité de la demande
    if not req.security_passed:
        r.append("filtres de sécurité non passés")
    if not req.data_fresh:
        r.append("données périmées ou incomplètes")
    if req.liquidity_usd is None:
        r.append("liquidité inconnue")
    elif req.liquidity_usd < cfg.min_liquidity_usd:
        r.append(f"liquidité {req.liquidity_usd} $ < {cfg.min_liquidity_usd} $")
    if not (ZERO < req.size_fraction <= 1):
        r.append(f"fraction de taille invalide : {req.size_fraction}")
    fees = req.est_round_trip_fees_usd
    if fees is None or fees < 0:
        r.append("frais estimés inconnus")
        fees = Decimal("Infinity")

    # 3. Portefeuille
    if req.token in state.busy_tokens:
        r.append("position ou ordre non résolu déjà présent sur ce token")
    if state.slots_used >= limits.max_concurrent_positions:
        r.append(f"{state.slots_used} position(s)/ordre(s) ≥ max {limits.max_concurrent_positions}")
    if state.trades_today >= limits.max_trades_per_day:
        r.append(f"{state.trades_today} trades aujourd'hui ≥ max {limits.max_trades_per_day}")
    if state.daily_loss_usd >= limits.daily_loss_limit_usd:
        r.append("perte journalière maximale atteinte")

    # 4. Cohérence solde réel ↔ ledger
    if req.wallet_balance_usd is None or req.ledger_balance_usd is None:
        r.append("solde du wallet ou du ledger indisponible")
        wallet = ZERO
    else:
        wallet = req.wallet_balance_usd
        if abs(req.wallet_balance_usd - req.ledger_balance_usd) > cfg.max_wallet_ledger_gap_usd:
            r.append(f"écart solde/ledger {req.wallet_balance_usd - req.ledger_balance_usd:+.2f} $")

    # 5. Taille au pire cas
    frac = req.size_fraction if ZERO < req.size_fraction <= 1 else ZERO
    size = min(limits.max_position_usd * frac, b_day - fees, b_cum - fees, wallet - fees)
    size = max(ZERO, size).quantize(CENT, rounding=ROUND_DOWN) if size.is_finite() else ZERO
    if size < limits.min_position_usd:
        r.append(f"budget insuffisant : taille possible {size} $ < minimum {limits.min_position_usd} $ "
                 f"(budget jour {b_day:.2f} $, cumul {b_cum:.2f} $)")

    approved = not r
    return RiskDecision(approved, size if approved else ZERO, tuple(r), b_day, b_cum)
