"""Enregistre des réponses GMGN réelles (lecture seule) comme fixtures de test.

Les listes sont tronquées à --keep éléments pour garder des fichiers légers.
Usage :
    python scripts/record_fixtures.py --demo
    python scripts/record_fixtures.py --out tests/fixtures/gmgn
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memepilot.gmgn.rate_limiter import RateLimiter  # noqa: E402
from memepilot.gmgn.read_client import GmgnError, GmgnReadClient  # noqa: E402
from memepilot.market.prices import SOL_MINT  # noqa: E402

DEMO_KEY = "gmgn_solbscbaseethmonadtron"


def trim(o: Any, keep: int) -> Any:
    if isinstance(o, list):
        return [trim(x, keep) for x in o[:keep]]
    if isinstance(o, dict):
        return {k: trim(v, keep) for k, v in o.items()}
    return o


async def main_async(args: argparse.Namespace) -> int:
    env = {"GMGN_API_KEY": DEMO_KEY} if args.demo else {}
    client = GmgnReadClient(args.cli, limiter=RateLimiter("free", safety=0.5), env_overrides=env)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    trending = await client.call(("market", "trending"), chain="sol", interval="1h", limit=args.keep)
    rows = (trending.data or {}).get("rank") or []
    if not rows:
        print("trending vide : abandon")
        return 1
    addr = rows[0]["address"]
    jobs: list[tuple[str, tuple[str, ...], dict[str, Any]]] = [
        ("trenches", ("market", "trenches"),
         {"chain": "sol", "type": ["new_creation", "completed"], "limit": args.keep}),
        ("kline_1m", ("market", "kline"), {"chain": "sol", "address": addr, "resolution": "1m"}),
        ("signal_sm", ("market", "signal"), {"chain": "sol", "signal_type": "12"}),
        ("track_smartmoney", ("track", "smartmoney"), {"chain": "sol", "limit": args.keep}),
        ("track_kol", ("track", "kol"), {"chain": "sol", "limit": args.keep}),
        ("token_holders", ("token", "holders"), {"chain": "sol", "address": addr, "limit": args.keep}),
        ("token_info", ("token", "info"), {"chain": "sol", "address": addr}),
        ("token_security", ("token", "security"), {"chain": "sol", "address": addr}),
        ("token_pool", ("token", "pool"), {"chain": "sol", "address": addr}),
        ("token_info_sol", ("token", "info"), {"chain": "sol", "address": SOL_MINT}),
    ]
    saved = {"market_trending": trending.data}
    for name, cmd, opts in jobs:
        try:
            res = await client.call(cmd, **opts)
            saved[name] = res.data
            print(f"✔ {name}")
        except GmgnError as exc:
            print(f"✘ {name} : {exc}")
    for name, data in saved.items():
        (out / f"{name}.json").write_text(
            json.dumps(trim(data, args.keep), ensure_ascii=False, indent=1), encoding="utf-8"
        )
    print(f"{len(saved)} fixtures écrites dans {out}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--demo", action="store_true")
    p.add_argument("--keep", type=int, default=5)
    p.add_argument("--out", default=str(ROOT / "tests" / "fixtures" / "gmgn" / "recorded"))
    p.add_argument("--cli", default=os.environ.get("GMGN_CLI_PATH", "gmgn-cli"))
    return asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
