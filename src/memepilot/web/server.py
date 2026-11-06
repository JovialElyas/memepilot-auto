"""Mini serveur HTTP local, LECTURE SEULE, pour l'interface live.

- Écoute par défaut sur 127.0.0.1 : invisible depuis le réseau. Sur un VPS, on y accède par un
  tunnel SSH (`ssh -L 8765:127.0.0.1:8765 ...`), jamais en ouvrant un port.
- Deux routes GET seulement : `/` (la page) et `/api/state` (l'instantané JSON). Aucune route
  ne modifie l'état : l'arrêt d'urgence reste `scripts/risk_admin.py halt`.
- Bibliothèque standard uniquement (asyncio), aucune dépendance ajoutée.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

PAGE = Path(__file__).with_name("live.html")
_HEADERS = (
    "X-Content-Type-Options: nosniff\r\n"
    "X-Frame-Options: DENY\r\n"
    "Referrer-Policy: no-referrer\r\n"
    "Cache-Control: no-store\r\n"
    "Content-Security-Policy: default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; connect-src 'self'\r\n"
)


def _response(status: str, body: bytes, ctype: str) -> bytes:
    head = (f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
            f"{_HEADERS}Connection: close\r\n\r\n")
    return head.encode() + body


def _default(o: Any) -> Any:
    return str(o)


class LiveServer:
    def __init__(self, get_state: Callable[[], dict[str, Any]], host: str = "127.0.0.1", port: int = 8765) -> None:
        self.get_state = get_state
        self.host = host
        self.port = port
        self._server: asyncio.base_events.Server | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]  # port réel si 0 (port libre choisi par l'OS)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def route(self, method: str, path: str) -> bytes:
        if method != "GET":
            return _response("405 Method Not Allowed", b"lecture seule", "text/plain; charset=utf-8")
        path = path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return _response("200 OK", PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/state":
            body = json.dumps(self.get_state(), default=_default, ensure_ascii=False).encode()
            return _response("200 OK", body, "application/json; charset=utf-8")
        return _response("404 Not Found", b"introuvable", "text/plain; charset=utf-8")

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            parts = line.decode("latin-1").split()
            # On lit (et ignore) les en-têtes, sans jamais accepter de corps de requête.
            while True:
                h = await asyncio.wait_for(reader.readline(), timeout=5)
                if h in (b"\r\n", b"\n", b""):
                    break
            resp = self.route(parts[0], parts[1]) if len(parts) >= 2 else \
                _response("400 Bad Request", b"", "text/plain")
            writer.write(resp)
            await writer.drain()
        except (TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()
