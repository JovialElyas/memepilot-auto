"""Génère la paire Ed25519 de signature des requêtes GMGN, sur TA machine.

- La clé privée est écrite directement dans ~/.config/gmgn/.env (jamais affichée).
- Seule la clé publique est affichée : c'est elle qu'on colle sur https://gmgn.ai/ai.
- Rappel : ce n'est PAS une clé de wallet. Aucune phrase de récupération n'est concernée.

Usage :
    python scripts/generate_signing_key.py              # crée la paire
    python scripts/generate_signing_key.py --set-api-key # enregistre la clé API (saisie masquée)
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import os
import re
import stat
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

CONFIG_DIR = Path.home() / ".config" / "gmgn"
ENV_FILE = CONFIG_DIR / ".env"


def _restrict(path: Path) -> None:
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    if os.name == "nt":
        print(
            "  ⚠ Windows : chmod n'a qu'un effet limité. Garde ce fichier dans ton profil "
            "utilisateur et ne le synchronise pas (OneDrive, etc.)."
        )


def generate() -> int:
    if ENV_FILE.exists() and "GMGN_PRIVATE_KEY" in ENV_FILE.read_text(encoding="utf-8"):
        print(f"Une clé existe déjà dans {ENV_FILE}. Rien n'a été modifié.")
        print("Pour la remplacer (rotation), renomme d'abord ce fichier.")
        return 1

    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    escaped = private_pem.strip().replace("\n", "\\n")
    with ENV_FILE.open("a", encoding="utf-8") as f:
        f.write("GMGN_API_KEY=\n")
        f.write(f'GMGN_PRIVATE_KEY="{escaped}\\n"\n')
    _restrict(ENV_FILE)

    print(f"✔ Clé privée de signature enregistrée dans {ENV_FILE} (non affichée).\n")
    print("Clé PUBLIQUE à coller sur https://gmgn.ai/ai (formulaire de création de clé API) :\n")
    print(public_pem)
    print("Ensuite : python scripts/generate_signing_key.py --set-api-key")
    return 0


KEYPAIR_FILE = CONFIG_DIR / "keypair.pem"  # créé par la commande officielle `gmgn-cli config`
_PRIV_BLOCK = re.compile(r"-----BEGIN PRIVATE KEY-----[\s\S]+?-----END PRIVATE KEY-----")


def _private_from_keypair() -> str | None:
    """Clé privée depuis keypair.pem (gmgn-cli config), sans jamais l'afficher."""
    if not KEYPAIR_FILE.exists():
        return None
    m = _PRIV_BLOCK.search(KEYPAIR_FILE.read_text(encoding="utf-8"))
    return m.group(0) if m else None


def set_api_key() -> int:
    """Enregistre la clé API (saisie masquée : rien dans l'historique PowerShell).

    Fonctionne après notre `generate` OU après la commande officielle `gmgn-cli config`
    (dans ce cas la clé privée est reprise depuis ~/.config/gmgn/keypair.pem).
    """
    existing = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    has_private = any(ln.startswith("GMGN_PRIVATE_KEY=") and len(ln) > 20 for ln in existing.splitlines())
    private_pem = None if has_private else _private_from_keypair()
    if not has_private and private_pem is None:
        print("Aucune clé de signature trouvée. Lance d'abord `gmgn-cli config` "
              "ou `python scripts/generate_signing_key.py`.")
        return 1
    api_key = getpass.getpass("Colle ta clé API GMGN (saisie masquée) : ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{8,200}", api_key):
        print("Format inattendu, rien n'a été enregistré.")
        return 1
    lines = [ln for ln in existing.splitlines() if not ln.startswith("GMGN_API_KEY=")]
    if private_pem is not None:
        lines = [ln for ln in lines if not ln.startswith("GMGN_PRIVATE_KEY=")]
        lines.append('GMGN_PRIVATE_KEY="' + private_pem.strip().replace("\r", "").replace("\n", "\\n") + '\\n"')
        print(f"  Clé de signature reprise depuis {KEYPAIR_FILE} (non affichée).")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines.insert(0, f"GMGN_API_KEY={api_key}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _restrict(ENV_FILE)
    print(f"✔ Clé API enregistrée dans {ENV_FILE}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set-api-key", action="store_true")
    args = parser.parse_args()
    return set_api_key() if args.set_api_key else generate()


if __name__ == "__main__":
    sys.exit(main())
