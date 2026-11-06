"""Validation des valeurs externes avant tout appel (SECURITY : données = données)."""

from __future__ import annotations

import re

_BASE58_SOL = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")


class InvalidAddressError(ValueError):
    pass


class InvalidArgumentError(ValueError):
    pass


def validate_sol_address(value: str) -> str:
    if not isinstance(value, str) or not _BASE58_SOL.fullmatch(value):
        raise InvalidAddressError("adresse Solana invalide (base58, 32 à 44 caractères attendus)")
    return value


def validate_simple_value(value: str) -> str:
    """Valeurs d'options simples (chain, interval, type, limit...)."""
    if not _SAFE_TOKEN.fullmatch(value):
        raise InvalidArgumentError(f"valeur d'option refusée : {value!r}")
    return value
