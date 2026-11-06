"""Client hors ligne : rejoue les fixtures réelles (interface et trading papier sans réseau ni clé)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from memepilot.gmgn.read_client import ArgValue, GmgnReadClient, GmgnResult, unwrap

FIX = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "gmgn"


class OfflineClient(GmgnReadClient):
    """Rejoue les fixtures réelles : permet de voir l'interface sans réseau ni clé."""

    def __init__(self) -> None:
        super().__init__("gmgn-cli")
        self.files: dict[tuple[str, ...], str] = {
            ("market", "trending"): "market_trending_sol_1h.json", ("market", "trenches"): "market_trenches.json",
            ("token", "security"): "token_security.json", ("token", "pool"): "token_pool.json",
            ("track", "smartmoney"): "track_smartmoney.json", ("track", "kol"): "track_kol.json",
            ("market", "kline"): "market_kline_1m.json",
        }

    async def call(self, command: tuple[str, ...], cache_ttl_s: float | None = None,
                   **options: ArgValue) -> GmgnResult:
        self.calls += 1
        name = "token_info_sol.json" if command == ("token", "info") else self.files[command]
        data = unwrap(json.loads((FIX / name).read_text(encoding="utf-8")))
        if command in (("token", "security"), ("token", "pool")) and isinstance(data, dict):
            data = {**data, "address": options.get("address")}
        return GmgnResult(command, data, 1.0, "", time.time())
