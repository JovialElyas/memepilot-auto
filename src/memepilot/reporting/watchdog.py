"""Chien de garde (D-037) : prévenir sur Telegram quand le bot ne travaille plus.

Trois cas :
1. le bot était ARRÊTÉ (PC éteint, fenêtre fermée, plantage) : au redémarrage, le dernier battement de cœur
   enregistré sur disque donne la durée de l'arrêt → message « arrêté de … à … » ;
2. le PC s'est mis en VEILLE : l'horloge saute d'un coup entre deux battements → message « pause détectée » ;
3. le bot tourne mais NE PRODUIT PLUS : aucun cycle complet depuis 5 min, ou positions ouvertes non surveillées
   depuis 1 min → message, puis « ✅ reprise » quand ça repart.
Pendant que le PC est éteint, le bot ne peut rien envoyer lui-même : HEALTHCHECK_URL (facultatif, par ex.
healthchecks.io) reçoit un signal à chaque battement ; ce service prévient s'il n'en reçoit plus.
"""

from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

BEAT_S = 30.0
DOWN_AFTER_S = 120.0  # un trou de plus de 2 min entre deux battements = arrêt ou veille
NO_CYCLE_ALERT_S = 300.0
NO_MONITOR_ALERT_S = 60.0


def _hm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _dur(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60} h {m % 60:02d}" if m >= 60 else f"{m} min"


def read_heartbeat(path: Path) -> float | None:
    try:
        return float(json.loads(path.read_text(encoding="utf-8"))["ts"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_heartbeat(path: Path, now: float) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"ts": now}), encoding="utf-8")
    tmp.replace(path)


def startup_message(previous: float | None, now: float) -> str | None:
    """Au démarrage : le bot était-il arrêté depuis longtemps ?"""
    if previous is None or now - previous <= DOWN_AFTER_S:
        return None
    return (f"⚠️ MemePilot était ARRÊTÉ de {_hm(previous)} à {_hm(now)} ({_dur(now - previous)}) : "
            "PC éteint ou en veille, ou bot fermé. Aucune position n'a été surveillée pendant ce temps.")


@dataclass
class Watchdog:
    last_beat: float
    alerts: dict[str, bool] = field(default_factory=dict)  # alerte en cours (évite de répéter le message)

    def check(self, now: float, started: float, last_cycle: float | None, last_monitor: float | None,
              open_positions: int) -> list[str]:
        out: list[str] = []
        if now - self.last_beat > DOWN_AFTER_S:
            out.append(f"⚠️ MemePilot : pause détectée de {_hm(self.last_beat)} à {_hm(now)} "
                       f"({_dur(now - self.last_beat)}), PC en veille ? Le bot reprend maintenant.")
        self.last_beat = now
        stale_cycle = now - (last_cycle or started) > NO_CYCLE_ALERT_S
        out += self._toggle("cycle", stale_cycle,
                            f"⚠️ MemePilot : aucun cycle d'analyse complet depuis {_dur(now - (last_cycle or started))} "
                            "(GMGN et relais limités ou en panne ?). Les positions restent surveillées.",
                            "✅ MemePilot : les cycles d'analyse ont repris.")
        stale_mon = open_positions > 0 and now - (last_monitor or started) > NO_MONITOR_ALERT_S
        out += self._toggle("monitor", stale_mon,
                            f"🚨 MemePilot : {open_positions} position(s) ouverte(s) NON surveillée(s) depuis "
                            f"{_dur(now - (last_monitor or started))} !",
                            "✅ MemePilot : la surveillance des positions a repris.")
        return out

    def _toggle(self, key: str, bad: bool, alert: str, ok: str) -> list[str]:
        was = self.alerts.get(key, False)
        self.alerts[key] = bad
        if bad and not was:
            return [alert]
        if was and not bad:
            return [ok]
        return []


def ping_healthcheck(url: str, timeout_s: float = 10.0) -> bool:
    """Signal « je suis vivant » vers un service externe (HTTPS seulement). Jamais bloquant pour le bot."""
    if not url.startswith("https://"):
        return False
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:  # noqa: S310 — https vérifié ci-dessus
            return bool(200 <= int(resp.status) < 300)
    except Exception:  # noqa: BLE001
        return False
