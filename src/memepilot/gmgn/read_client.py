"""Client GMGN en LECTURE SEULE (option A, D-003) : wrapper de `gmgn-cli --raw`.

Garanties :
- seules les sous-commandes de la liste blanche READ_ONLY_COMMANDS sont exécutables ;
  swap / order / cooking / multi-swap sont impossibles depuis ce module ;
- pas de shell : arguments passés en liste, adresses validées ;
- les secrets ne transitent jamais par Python (gmgn-cli lit ~/.config/gmgn/.env) ;
- chaque appel passe par le régulateur de débit global (G-03) ;
- chaque résultat est horodaté (fetched_at) pour le contrôle de fraîcheur (F-04) ;
- le réessai automatique interne de gmgn-cli est désactivé : seul notre régulateur décide.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from memepilot.gmgn.rate_limiter import PlanCapacityError, RateLimiter, parse_rate_limit
from memepilot.gmgn.validation import validate_simple_value, validate_sol_address
from memepilot.logging_utils import redact

READ_ONLY_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("token", "info"),
        ("token", "security"),
        ("token", "pool"),
        ("token", "holders"),
        ("token", "traders"),
        ("market", "kline"),
        ("market", "trending"),
        ("market", "trenches"),
        ("market", "signal"),
        ("market", "hot-searches"),
        ("portfolio", "info"),
        ("portfolio", "holdings"),
        ("portfolio", "activity"),
        ("portfolio", "stats"),
        ("portfolio", "profits"),
        ("portfolio", "token-balance"),
        ("portfolio", "created-tokens"),
        ("track", "kol"),
        ("track", "smartmoney"),
        ("track", "follow-wallet"),
        ("track", "follow-tokens"),
        ("gas-price",),
    }
)

ADDRESS_OPTIONS = frozenset({"address", "wallet", "token"})
ArgValue = str | int | float | bool | Sequence[str]


class GmgnError(RuntimeError):
    pass


class GmgnCommandNotAllowed(GmgnError):
    pass


class GmgnRateLimited(GmgnError):
    def __init__(self, message: str, wait_s: float = 0.0, fatal: bool = False) -> None:
        super().__init__(message)
        self.wait_s = wait_s
        self.fatal = fatal


class GmgnPlanUpgradeRequired(GmgnError):
    pass


class GmgnCallFailed(GmgnError):
    pass


@dataclass(frozen=True)
class GmgnResult:
    command: tuple[str, ...]
    data: Any
    latency_ms: float
    notices: str
    fetched_at: float  # epoch (s) au moment de la réception
    from_cache: bool = False


def _fmt_number(v: float) -> str:
    s = repr(float(v))
    return s[:-2] if s.endswith(".0") else s


def build_argv(cli: str, command: tuple[str, ...], options: Mapping[str, ArgValue]) -> list[str]:
    if command not in READ_ONLY_COMMANDS:
        raise GmgnCommandNotAllowed(f"commande non autorisée en lecture seule : {' '.join(command)}")
    argv = [cli, *command]
    for key, value in options.items():
        validate_simple_value(key)
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                argv.append(flag)
            continue
        if isinstance(value, float):
            values = [_fmt_number(value)]
        elif isinstance(value, (str, int)):
            values = [str(value)]
        else:
            values = [str(v) for v in value]
        for sv in values:
            if key in ADDRESS_OPTIONS:
                validate_sol_address(sv)
            else:
                validate_simple_value(sv)
            argv += [flag, sv]
    argv.append("--raw")
    return argv


def unwrap(payload: Any) -> Any:
    """Certaines commandes renvoient {code, data, message}, d'autres l'objet directement.

    gmgn-cli peut sortir en code 0 avec un `code` métier ≠ 0 (quota, erreur passagère) :
    sans ce contrôle, l'erreur passerait pour une liste vide (idée R1 de la démo).
    """
    if isinstance(payload, dict) and "code" in payload and (
        "data" in payload or "message" in payload or "msg" in payload or "error" in payload
    ):
        if payload.get("code") not in (0, "0"):
            msg = payload.get("message") or payload.get("msg") or payload.get("error") or ""
            raise GmgnCallFailed(f"code GMGN {payload.get('code')}: {msg}".strip())
        return payload.get("data")
    return payload


class GmgnReadClient:
    def __init__(
        self,
        cli_path: str = "gmgn-cli",
        limiter: RateLimiter | None = None,
        timeout_s: float = 25.0,
        env_overrides: Mapping[str, str] | None = None,
    ) -> None:
        # shutil.which résout aussi gmgn-cli.cmd sous Windows (PATHEXT).
        self._cli = shutil.which(cli_path) or cli_path
        self.limiter = limiter or RateLimiter("free")
        self._timeout = timeout_s
        self._env = {**os.environ, "GMGN_RATE_LIMIT_AUTO_RETRY_MAX_WAIT_MS": "0", **(env_overrides or {})}
        self._cache: dict[tuple[str, ...], GmgnResult] = {}
        self.calls = 0

    def can_serve(self, command: tuple[str, ...]) -> bool:
        """Faut-il tenter cette commande maintenant ? (faux pendant un gel : inutile d'allonger le bannissement)"""
        return self.limiter.frozen_for <= 0

    def kline_budget(self, wanted: int) -> int:
        """Nombre de séries de bougies à demander ce cycle."""
        return wanted

    async def call(
        self, command: tuple[str, ...], cache_ttl_s: float | None = None, **options: ArgValue
    ) -> GmgnResult:
        argv = build_argv(self._cli, command, options)
        key = tuple(argv)
        if cache_ttl_s:
            hit = self._cache.get(key)
            if hit and time.time() - hit.fetched_at < cache_ttl_s:
                return GmgnResult(hit.command, hit.data, 0.0, hit.notices, hit.fetched_at, from_cache=True)

        frozen = self.limiter.frozen_for
        if frozen > 0:
            # Échec immédiat pendant un gel (après un 429) : le cycle s'arrête proprement et la boucle
            # suivante réessaiera, au lieu d'attendre jusqu'à 60 s au milieu d'un cycle.
            raise GmgnRateLimited(f"gel en cours — {frozen:.0f}s restantes", frozen, False)
        try:
            await self.limiter.acquire(command)
        except PlanCapacityError as exc:
            raise GmgnPlanUpgradeRequired(str(exc)) from exc

        self.calls += 1
        start = time.perf_counter()
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=self._env
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise GmgnCallFailed(f"délai dépassé ({self._timeout}s) : {' '.join(command)}") from exc
        latency_ms = (time.perf_counter() - start) * 1000
        fetched_at = time.time()
        stderr = redact(err.decode("utf-8", "replace")).strip()

        if proc.returncode != 0:
            if "PLAN_UPGRADE_REQUIRED" in stderr:
                raise GmgnPlanUpgradeRequired(stderr[:500])
            rl = parse_rate_limit(stderr)
            if rl is not None:
                self.limiter.freeze(rl.wait_s)
                raise GmgnRateLimited(f"{rl.kind} — gel {rl.wait_s:.0f}s", rl.wait_s, rl.fatal)
            raise GmgnCallFailed(f"{' '.join(command)} a échoué : {stderr[:500]}")
        try:
            payload = json.loads(out)
        except json.JSONDecodeError as exc:
            raise GmgnCallFailed(f"réponse non-JSON pour {' '.join(command)}") from exc
        result = GmgnResult(command, unwrap(payload), latency_ms, stderr, fetched_at)
        if cache_ttl_s:
            self._cache[key] = result
        return result
