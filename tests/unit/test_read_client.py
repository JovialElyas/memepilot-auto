import json
import sys
from pathlib import Path

import pytest

from memepilot.gmgn.read_client import (
    READ_ONLY_COMMANDS,
    GmgnCallFailed,
    GmgnCommandNotAllowed,
    GmgnRateLimited,
    GmgnReadClient,
    build_argv,
    unwrap,
)
from memepilot.gmgn.validation import InvalidAddressError, InvalidArgumentError

SOL = "So11111111111111111111111111111111111111112"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gmgn"


@pytest.mark.parametrize(
    "cmd",
    [
        ("swap",),
        ("multi-swap",),
        ("order", "get"),
        ("order", "quote"),
        ("order", "strategy", "create"),
        ("order", "strategy", "cancel"),
        ("cooking", "create"),
        ("config",),
    ],
)
def test_trading_commands_are_forbidden(cmd: tuple[str, ...]) -> None:
    with pytest.raises(GmgnCommandNotAllowed):
        build_argv("gmgn-cli", cmd, {})


def test_allowlist_has_no_trading_verbs() -> None:
    forbidden = {"swap", "multi-swap", "order", "cooking", "strategy", "config"}
    assert not any(set(c) & forbidden for c in READ_ONLY_COMMANDS)


def test_argv_building() -> None:
    argv = build_argv(
        "gmgn-cli",
        ("market", "trenches"),
        {"chain": "sol", "type": ["new_creation", "completed"], "limit": 5, "raw_flag": False},
    )
    assert argv == [
        "gmgn-cli", "market", "trenches", "--chain", "sol",
        "--type", "new_creation", "--type", "completed", "--limit", "5", "--raw",
    ]


@pytest.mark.parametrize("bad", ["abc", "0x" + "a" * 40, SOL + "; rm -rf /", "O" * 44])
def test_bad_addresses_rejected(bad: str) -> None:
    with pytest.raises(InvalidAddressError):
        build_argv("gmgn-cli", ("token", "info"), {"chain": "sol", "address": bad})


@pytest.mark.parametrize("bad", ["sol;ls", "$(whoami)", "a b", ""])
def test_bad_option_values_rejected(bad: str) -> None:
    with pytest.raises(InvalidArgumentError):
        build_argv("gmgn-cli", ("market", "trending"), {"chain": bad})


def test_unwrap_both_shapes() -> None:
    wrapped = json.loads((FIXTURES / "market_trending_sol_1h.json").read_text(encoding="utf-8"))
    assert "rank" in unwrap(wrapped)
    direct = json.loads((FIXTURES / "token_security.json").read_text(encoding="utf-8"))
    assert unwrap(direct) is direct
    with pytest.raises(GmgnCallFailed):
        unwrap({"code": 40001, "data": None, "message": "bad"})


def _fake_cli(tmp_path: Path, body: str) -> str:
    """Crée un faux gmgn-cli (script Python) pour tester le client sans réseau."""
    script = tmp_path / "fake_cli.py"
    script.write_text(body, encoding="utf-8")
    if sys.platform == "win32":
        launcher = tmp_path / "fake.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        launcher = tmp_path / "fake"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        launcher.chmod(0o755)
    return str(launcher)


async def test_client_success(tmp_path: Path) -> None:
    fixture = (FIXTURES / "portfolio_info_demo.json").as_posix()
    cli = _fake_cli(tmp_path, f"import sys;print(open({fixture!r}).read())")
    res = await GmgnReadClient(cli).call(("portfolio", "info"))
    assert res.data == {"wallets": []}
    assert res.latency_ms > 0


async def test_client_rate_limited(tmp_path: Path) -> None:
    cli = _fake_cli(
        tmp_path, "import sys;sys.stderr.write('[gmgn-cli] 429 RATE_LIMIT_BANNED');sys.exit(1)"
    )
    with pytest.raises(GmgnRateLimited):
        await GmgnReadClient(cli).call(("market", "trending"), chain="sol")


async def test_client_error_is_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMGN_API_KEY", "supersecretkey123")
    cli = _fake_cli(tmp_path, "import sys;sys.stderr.write('bad key supersecretkey123');sys.exit(1)")
    with pytest.raises(GmgnCallFailed) as exc:
        await GmgnReadClient(cli).call(("market", "trending"), chain="sol")
    assert "supersecretkey123" not in str(exc.value)


async def test_plan_upgrade_required_is_distinct(tmp_path: Path) -> None:
    from memepilot.gmgn.read_client import GmgnPlanUpgradeRequired

    cli = _fake_cli(tmp_path, "import sys;sys.stderr.write('HTTP 403 error=PLAN_UPGRADE_REQUIRED');sys.exit(1)")
    with pytest.raises(GmgnPlanUpgradeRequired):
        await GmgnReadClient(cli).call(("market", "trending"), chain="sol")


async def test_rate_limit_freezes_shared_limiter(tmp_path: Path) -> None:
    msg = (FIXTURES / "stderr_trenches_banned.txt").read_text(encoding="utf-8")
    cli = _fake_cli(tmp_path, f"import sys;sys.stderr.write({msg!r});sys.exit(1)")
    client = GmgnReadClient(cli)
    with pytest.raises(GmgnRateLimited) as exc:
        await client.call(("market", "trenches"), chain="sol")
    assert exc.value.wait_s > 1
    assert client.limiter.frozen_for > 1  # plus aucun appel avant la fin du gel


async def test_cache_and_env(tmp_path: Path) -> None:
    counter = tmp_path / "n.txt"
    body = (
        f"import os,json,pathlib;p=pathlib.Path({str(counter)!r});n=int(p.read_text()) if p.exists() else 0;"
        "p.write_text(str(n+1));"
        "print(json.dumps({'retry':os.environ.get('GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS'),'n':n+1}))"
    )
    client = GmgnReadClient(_fake_cli(tmp_path, body))
    r1 = await client.call(("token", "info"), cache_ttl_s=30, chain="sol", address=SOL)
    r2 = await client.call(("token", "info"), cache_ttl_s=30, chain="sol", address=SOL)
    assert r1.data["retry"] == "0"  # réessai interne de gmgn-cli désactivé
    assert r2.from_cache and r2.data["n"] == 1 and client.calls == 1
    assert r2.fetched_at == r1.fetched_at  # le cache ne rajeunit pas la donnée


def test_float_options_formatting() -> None:
    argv = build_argv("gmgn-cli", ("market", "trenches"), {"max_rug_ratio": 0.3, "min_liquidity": 10000.0})
    assert argv[3:] == ["--max-rug-ratio", "0.3", "--min-liquidity", "10000", "--raw"]
