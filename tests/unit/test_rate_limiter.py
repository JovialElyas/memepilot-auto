from pathlib import Path

import pytest

from memepilot.gmgn.rate_limiter import (
    ROUTE_WEIGHTS,
    PlanCapacityError,
    RateLimiter,
    parse_rate_limit,
)
from memepilot.gmgn.read_client import READ_ONLY_COMMANDS

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "gmgn"


class FakeTime:
    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def make(plan: str = "free", safety: float = 1.0) -> tuple[RateLimiter, FakeTime]:
    ft = FakeTime()
    return RateLimiter(plan, safety=safety, clock=ft.clock, sleep=ft.sleep), ft


def test_every_read_command_has_official_weight() -> None:
    assert set(ROUTE_WEIGHTS) >= READ_ONLY_COMMANDS


async def test_burst_within_capacity_does_not_wait() -> None:
    rl, ft = make()
    for _ in range(5):
        await rl.acquire(("token", "info"))  # poids 1 × 5 = capacité Free
    assert ft.slept == []


async def test_waits_when_bucket_empty() -> None:
    rl, ft = make()
    await rl.acquire(("market", "trending"))  # 3
    await rl.acquire(("market", "trending"))  # 3 → il manque 1 jeton
    assert ft.slept and abs(sum(ft.slept) - 0.2) < 1e-9  # 1 jeton / 5 par s


async def test_never_exceeds_capacity_over_time() -> None:
    rl, ft = make(safety=0.8)
    spent, start = 0, ft.t
    for _ in range(60):
        spent += await rl.acquire(("token", "holders"))  # 5
    elapsed = ft.t - start
    # jetons consommés ≤ capacité initiale + débit × temps écoulé
    assert spent <= rl.capacity + rl.rate * elapsed + 1e-6


async def test_swap_weight_impossible_on_free() -> None:
    rl, _ = make("free")
    with pytest.raises(PlanCapacityError):
        await rl.acquire(("track", "follow-wallet"))  # poids 10 > capacité 5
    rl_plus, _ = make("plus")
    await rl_plus.acquire(("track", "follow-wallet"))


async def test_freeze_blocks_all_calls() -> None:
    rl, ft = make()
    rl.freeze(20)
    t0 = ft.t
    await rl.acquire(("token", "info"))
    assert ft.t - t0 >= 20


def test_parse_real_banned_message() -> None:
    msg = (FIX / "stderr_trenches_banned.txt").read_text(encoding="utf-8")
    info = parse_rate_limit(msg)
    assert info is not None
    assert info.kind == "RATE_LIMIT_BANNED"
    assert 1 < info.wait_s <= 30
    assert not info.fatal


def test_parse_variants() -> None:
    mx = parse_rate_limit("HTTP 429 code=429 error=RATE_LIMIT_BANNED_MAX message=banned")
    assert mx is not None and mx.fatal and mx.wait_s >= 300
    ra = parse_rate_limit('{"code":429,"error":"RATE_LIMIT_EXCEEDED","reset_at":1000060}', now_epoch=1000000)
    assert ra is not None and 60 <= ra.wait_s <= 62
    assert parse_rate_limit("HTTP 401 unauthorized") is None
