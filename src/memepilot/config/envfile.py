"""Lecture minimale du fichier `.env` du PROJET, limitée aux préfixes autorisés.

Les identifiants GMGN ne sont jamais lus ici (gmgn-cli lit lui-même ~/.config/gmgn/.env).
Les variables déjà présentes dans l'environnement sont prioritaires.
"""

from __future__ import annotations

import os
from pathlib import Path

ALLOWED_PREFIXES = ("TELEGRAM_", "MEMEPILOT_")


def load_project_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            if key.startswith(ALLOWED_PREFIXES):
                values[key] = val.strip().strip('"').strip("'")
    for key, val in os.environ.items():
        if key.startswith(ALLOWED_PREFIXES):
            values[key] = val
    return values
