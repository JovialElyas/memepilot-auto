"""Configure Telegram : trouve ton CHAT ID automatiquement et envoie un message de test.

Prérequis : TELEGRAM_BOT_TOKEN dans le fichier .env du projet (jamais dans un chat ni dans Git).

Usage (PowerShell) :
    python scripts\\telegram_setup.py           # vérifie le jeton, cherche ton chat, propose de l'enregistrer
    python scripts\\telegram_setup.py --test    # envoie seulement un message de test
Avant : ouvre Telegram, cherche ton bot (nom affiché par le script), appuie sur « Démarrer » / envoie « bonjour ».
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memepilot.config.envfile import load_project_env  # noqa: E402
from memepilot.logging_utils import redact  # noqa: E402
from memepilot.reporting.telegram import TelegramNotifier  # noqa: E402

ENV = ROOT / ".env"


def api(token: str, method: str) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    with urllib.request.urlopen(url, timeout=15) as r:  # noqa: S310 — URL https fixe
        data: dict[str, Any] = json.loads(r.read())
    if not data.get("ok"):
        raise RuntimeError(f"Telegram a refusé {method} : {data.get('description')}")
    return data


def find_chats(updates: list[dict[str, Any]]) -> dict[str, str]:
    chats: dict[str, str] = {}
    for u in updates:
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (u.get(key) or {}).get("chat")
            if chat and "id" in chat:
                name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
                chats[str(chat["id"])] = f"{name or '?'} ({chat.get('type', '?')})"
    return chats


def write_env(key: str, value: str) -> None:
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    lines = [ln for ln in lines if not ln.startswith(f"{key}=")] + [f"{key}={value}"]
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser(description="Configuration Telegram (notifications sortantes)")
    p.add_argument("--test", action="store_true")
    a = p.parse_args()
    env = load_project_env(ENV)
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print(f"Ajoute d'abord une ligne TELEGRAM_BOT_TOKEN=... dans {ENV} (copie de .env.example), puis relance.")
        return 1
    try:
        me = api(token, "getMe")["result"]
    except Exception as exc:  # noqa: BLE001
        print(f"Jeton invalide ou réseau indisponible : {redact(str(exc).replace(token, '***'))}")
        return 1
    print(f"✔ Bot reconnu : @{me.get('username')}")

    chat_id = env.get("TELEGRAM_CHAT_ID", "")
    if not a.test:
        chats = find_chats(api(token, "getUpdates").get("result", []))
        if not chats:
            print(f"Aucun message reçu. Dans Telegram : cherche @{me.get('username')}, appuie sur « Démarrer » "
                  "(ou envoie « bonjour »), puis relance ce script.")
            return 2
        for cid, label in chats.items():
            print(f"  chat trouvé : {cid}  →  {label}")
        chat_id = next(iter(chats)) if len(chats) == 1 else input("CHAT ID à utiliser : ").strip()
        if input(f"Enregistrer TELEGRAM_CHAT_ID={chat_id} dans .env ? [o/N] ").strip().lower() in ("o", "oui", "y"):
            write_env("TELEGRAM_CHAT_ID", chat_id)
            print("✔ Enregistré.")
    if not chat_id:
        print("TELEGRAM_CHAT_ID manquant : relance sans --test.")
        return 1
    n = TelegramNotifier(token, chat_id, min_interval_s=0)
    asyncio.run(n.send("✅ MemePilot : notifications Telegram configurées (mode papier)."))
    print("✔ Message de test envoyé." if not n.failures else f"✘ Échec d'envoi : {n.last_error}")
    return 0 if not n.failures else 1


if __name__ == "__main__":
    sys.exit(main())
