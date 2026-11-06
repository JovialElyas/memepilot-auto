"""Notifications Telegram — SORTANTES uniquement (D-007). Aucune commande n'est lue depuis Telegram.

Une notification ne doit JAMAIS interrompre la boucle de trading : toute erreur est absorbée et comptée.
Le jeton du bot n'apparaît jamais dans les logs (masqué par logging_utils.redact).
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from memepilot.logging_utils import redact

MAX_LEN = 3900


class Notifier(Protocol):
    async def send(self, text: str) -> None: ...


@dataclass
class NullNotifier:
    """Utilisé quand Telegram n'est pas configuré (et dans les tests) : garde les messages en mémoire."""

    sent: list[str] = field(default_factory=list)

    async def send(self, text: str) -> None:
        self.sent.append(text[:MAX_LEN])


Opener = Callable[[urllib.request.Request, float], bytes]


def _default_opener(req: urllib.request.Request, timeout: float) -> bytes:
    if not req.full_url.startswith("https://api.telegram.org/"):
        raise ValueError("URL Telegram inattendue")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — schéma https vérifié ci-dessus
        return bytes(resp.read())


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, opener: Opener = _default_opener,
                 min_interval_s: float = 1.1, timeout_s: float = 10.0) -> None:
        if not bot_token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID sont requis")
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat = chat_id
        self._open = opener
        self._min = min_interval_s
        self._timeout = timeout_s
        self._last = 0.0
        self.failures = 0
        self.last_error = ""

    def _post(self, text: str) -> None:
        body = json.dumps({"chat_id": self._chat, "text": text[:MAX_LEN], "disable_web_page_preview": True}).encode()
        req = urllib.request.Request(self._url, data=body, headers={"Content-Type": "application/json"})  # noqa: S310
        raw = self._open(req, self._timeout)
        if not json.loads(raw or b"{}").get("ok", False):
            raise RuntimeError("réponse Telegram non ok")

    async def send(self, text: str) -> None:
        wait = self._min - (time.monotonic() - self._last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            await asyncio.to_thread(self._post, redact(text))
        except Exception as exc:  # noqa: BLE001 — une notification ne casse jamais la boucle
            self.failures += 1
            self.last_error = redact(str(exc))[:200]
        finally:
            self._last = time.monotonic()


def notifier_from_env(env: dict[str, str]) -> Notifier:
    token, chat = env.get("TELEGRAM_BOT_TOKEN", "").strip(), env.get("TELEGRAM_CHAT_ID", "").strip()
    return TelegramNotifier(token, chat) if token and chat else NullNotifier()
