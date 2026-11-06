"""Phase 1 — Vérification du wallet lié à la clé API GMGN (SECURITY §4, D-009).

LECTURE SEULE : n'exécute aucun ordre, ne lit aucun secret.

Usage :
    python scripts/check_wallet_binding.py --demo
        Test de connectivité avec la clé publique de démo GMGN (lecture seule).
    python scripts/check_wallet_binding.py
        Vérifie les wallets liés à TA clé API (après configuration de ~/.config/gmgn/.env).
    python scripts/check_wallet_binding.py --expect <adresse_wallet_trading_GMGN>
        Confirme en plus que l'adresse attendue figure bien parmi les wallets liés.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from memepilot.gmgn.read_client import GmgnError, GmgnReadClient  # noqa: E402
from memepilot.gmgn.validation import InvalidAddressError, validate_sol_address  # noqa: E402

DEMO_KEY = "gmgn_solbscbaseethmonadtron"  # clé publique documentée par GMGN, lecture seule


def extract_wallets(data: Any) -> list[dict[str, Any]]:
    wallets = data.get("wallets", []) if isinstance(data, dict) else []
    out = []
    for w in wallets if isinstance(wallets, list) else []:
        if not isinstance(w, dict):
            continue
        addr = w.get("address") or w.get("wallet_address") or ""
        out.append({"address": str(addr), "chain": w.get("chain", "?"), "raw": w})
    return out


async def run(args: argparse.Namespace) -> int:
    if args.demo:
        os.environ["GMGN_API_KEY"] = DEMO_KEY
    client = GmgnReadClient(args.cli)

    print("1) Connectivité (market trending, lecture seule)…")
    try:
        r = await client.call(("market", "trending"), chain="sol", interval="1h", limit=3)
    except GmgnError as exc:
        print(f"   ✘ Échec : {exc}")
        print("   Pistes : gmgn-cli installé ? clé API valide ? connexion IPv4 ? horloge à l'heure ?")
        return 2
    print(f"   ✔ OK ({r.latency_ms:.0f} ms)")
    if args.demo:
        print("\nMode démo : connectivité confirmée. Configure ta propre clé pour l'étape 2.")
        return 0

    print("2) Wallets liés à la clé API (portfolio info)…")
    try:
        info = await client.call(("portfolio", "info"))
    except GmgnError as exc:
        print(f"   ✘ Échec : {exc}")
        return 2
    wallets = extract_wallets(info.data)
    sol = [w for w in wallets if _is_sol(w["address"])]
    for w in wallets:
        print(f"   - chain={w['chain']} address={w['address']}")
    if args.verbose:
        print(json.dumps(info.data, indent=2, ensure_ascii=False))

    print("\nRésultat :")
    if not sol:
        print("   CAS C — aucun wallet Solana lié à la clé API → STOP (aucun contournement).")
        print("   Vérifie sur gmgn.ai que le wallet de trading existe et que la clé y est liée.")
        return 3
    if args.expect:
        if any(w["address"] == args.expect for w in sol):
            print("   ✔ CAS B confirmé — le wallet de trading GMGN attendu est lié à la clé API.")
        else:
            print("   ✘ L'adresse attendue n'apparaît pas parmi les wallets liés → STOP.")
            return 4
    else:
        print("   Wallet(s) Solana lié(s) trouvé(s). Relance avec --expect <adresse> pour confirmer")
        print("   qu'il s'agit bien du wallet de trading GMGN que tu alimentes.")
    print("\nÉtape manuelle restante : vérifier sur gmgn.ai/ai les permissions de la clé")
    print("(lecture seule pour l'instant ; le trading + whitelist IP viendront en Phase 6).")
    return 0


def _is_sol(addr: str) -> bool:
    try:
        validate_sol_address(addr)
        return True
    except InvalidAddressError:
        return False


def main() -> int:
    p = argparse.ArgumentParser(description="Vérification du wallet lié à la clé API GMGN")
    p.add_argument("--demo", action="store_true", help="tester avec la clé de démo publique")
    p.add_argument("--expect", help="adresse publique du wallet de trading GMGN attendu")
    p.add_argument("--cli", default=os.environ.get("GMGN_CLI_PATH", "gmgn-cli"))
    p.add_argument("--verbose", action="store_true", help="afficher la réponse complète")
    args = p.parse_args()
    if args.expect:
        validate_sol_address(args.expect)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
