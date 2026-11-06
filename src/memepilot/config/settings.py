"""Paramètres d'exécution lus depuis l'environnement (aucun secret GMGN ici)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from memepilot.gmgn.validation import InvalidAddressError, validate_sol_address


class Mode(StrEnum):
    PAPER = "paper"
    LIVE = "live"


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    mode: Mode
    limits_path: Path
    db_path: Path
    gmgn_cli_path: str
    trading_wallet: str | None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = dict(os.environ if env is None else env)
        raw_mode = e.get("MEMEPILOT_MODE", "paper").strip().lower()
        try:
            mode = Mode(raw_mode)
        except ValueError as exc:
            raise SettingsError(f"MEMEPILOT_MODE invalide : {raw_mode!r}") from exc

        wallet = e.get("GMGN_TRADING_WALLET", "").strip() or None
        if wallet is not None:
            try:
                validate_sol_address(wallet)
            except InvalidAddressError as exc:
                raise SettingsError(f"GMGN_TRADING_WALLET invalide : {exc}") from exc

        if mode is Mode.LIVE:
            # Garde-fou de Phase 1 : le live n'existe pas encore (ROADMAP, Phase 7).
            raise SettingsError("le mode live est désactivé jusqu'à la Phase 7")

        return cls(
            mode=mode,
            limits_path=Path(e.get("MEMEPILOT_LIMITS_PATH", "config/limits.yaml")),
            db_path=Path(e.get("MEMEPILOT_DB_PATH", "data/memepilot.db")),
            gmgn_cli_path=e.get("GMGN_CLI_PATH", "gmgn-cli"),
            trading_wallet=wallet,
        )
