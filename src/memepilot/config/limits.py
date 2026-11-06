"""Limites financières : chargement, validation stricte et gel (SPEC F-23, D-010).

Les limites sont un objet immuable. Aucun code ne peut les modifier après
chargement ; leur empreinte (fingerprint) sera enregistrée dans le ledger
pour détecter tout changement entre deux démarrages en mode live.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# Plafond absolu codé en dur. La configuration peut être PLUS stricte, jamais plus large.
ABSOLUTE_MAX_CUMULATIVE_LOSS_USD = Decimal("20")
ABSOLUTE_MAX_SLIPPAGE_PCT = 50
ABSOLUTE_MAX_ORDER_RETRIES = 1


class LimitsError(ValueError):
    """Configuration de limites invalide : le système ne doit pas démarrer."""


class Limits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=False)

    initial_capital_usd: Decimal = Field(gt=0)
    maximum_cumulative_loss_usd: Decimal = Field(gt=0)
    daily_loss_limit_usd: Decimal = Field(gt=0)

    max_position_usd: Decimal = Field(gt=0)
    min_position_usd: Decimal = Field(gt=0)
    max_concurrent_positions: int = Field(ge=1, le=10)
    max_trades_per_day: int = Field(ge=1, le=100)

    max_slippage_pct: int = Field(ge=1, le=ABSOLUTE_MAX_SLIPPAGE_PCT)
    max_order_retries: int = Field(ge=0, le=ABSOLUTE_MAX_ORDER_RETRIES)

    leverage: Literal[False]
    automatic_reloading: Literal[False]
    external_withdrawals: Literal[False]

    @model_validator(mode="after")
    def _coherence(self) -> Limits:
        if self.maximum_cumulative_loss_usd > ABSOLUTE_MAX_CUMULATIVE_LOSS_USD:
            raise ValueError(
                f"maximum_cumulative_loss_usd ({self.maximum_cumulative_loss_usd}) dépasse "
                f"le plafond absolu de {ABSOLUTE_MAX_CUMULATIVE_LOSS_USD} USD"
            )
        if self.maximum_cumulative_loss_usd >= self.initial_capital_usd:
            raise ValueError("la perte cumulée max doit être inférieure au capital initial")
        if self.daily_loss_limit_usd > self.maximum_cumulative_loss_usd:
            raise ValueError("la perte journalière max ne peut dépasser la perte cumulée max")
        if self.min_position_usd > self.max_position_usd:
            raise ValueError("min_position_usd > max_position_usd")
        if self.max_position_usd > self.daily_loss_limit_usd:
            # D-010 : une seule position qui tombe à zéro ne doit pas dépasser la limite du jour.
            raise ValueError("max_position_usd ne peut dépasser daily_loss_limit_usd (D-010)")
        return self

    def fingerprint(self) -> str:
        """Empreinte SHA-256 canonique des limites (pour le ledger et le contrôle au démarrage)."""
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_limits(path: str | Path) -> Limits:
    p = Path(path)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LimitsError(f"fichier de limites introuvable : {p}") from exc
    if not isinstance(raw, dict):
        raise LimitsError("le fichier de limites doit contenir un objet YAML")
    # Les montants passent par str() pour éviter les erreurs d'arrondi des float YAML.
    normalized = {k: (str(v) if isinstance(v, float) else v) for k, v in raw.items()}
    try:
        return Limits.model_validate(normalized)
    except ValueError as exc:
        raise LimitsError(str(exc)) from exc
