"""Filtrage des secrets dans tout texte journalisé (SECURITY §2)."""

from __future__ import annotations

import os
import re

_PEM = re.compile(r"-----BEGIN [A-Z ]*KEY-----.*?-----END [A-Z ]*KEY-----", re.DOTALL)
_PEM_ESCAPED = re.compile(r"-----BEGIN [A-Z ]*KEY-----(?:\\n|[^\n])*?-----END [A-Z ]*KEY-----")
_SECRET_ENV = ("GMGN_API_KEY", "GMGN_PRIVATE_KEY", "TELEGRAM_BOT_TOKEN")
_KV = re.compile(r"((?:GMGN_API_KEY|GMGN_PRIVATE_KEY|TELEGRAM_BOT_TOKEN|X-APIKEY)\s*[=:]\s*)\S+")

MASK = "***REDACTED***"


def redact(text: str) -> str:
    text = _PEM.sub(MASK, text)
    text = _PEM_ESCAPED.sub(MASK, text)
    text = _KV.sub(lambda m: m.group(1) + MASK, text)
    for name in _SECRET_ENV:
        value = os.environ.get(name, "")
        if len(value) >= 8:
            text = text.replace(value, MASK)
    return text
