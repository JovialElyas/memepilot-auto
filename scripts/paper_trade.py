"""Phase 5 — Trading PAPIER continu : boucle complète + interface + Telegram + rapport hebdomadaire.

Aucun ordre réel n'est possible : le moteur n'a accès qu'au client GMGN en lecture seule.

Usage (PowerShell) :
    python scripts\\paper_trade.py --demo                  # scan toutes les 30 s, positions surveillées toutes les 5 s
    python scripts\\paper_trade.py --offline --cycles 3    # démonstration sans réseau
    python scripts\\paper_trade.py --demo --fresh          # repartir d'un portefeuille papier neuf (archive l'ancien)
    python scripts\\paper_trade.py --demo --profile strict # stratégie stricte (celle qui serait utilisée en réel)
Interface live : http://127.0.0.1:8765 (mise à jour toutes les 2 s). Base : data\\paper.db (ledger + état de risque).
Telegram (facultatif) : TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID dans le .env du projet.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memepilot.config.envfile import load_project_env  # noqa: E402
from memepilot.config.limits import load_limits  # noqa: E402
from memepilot.engine.cycle import CycleConfig  # noqa: E402
from memepilot.engine.offline import OfflineClient  # noqa: E402
from memepilot.engine.paper import PaperEngine  # noqa: E402
from memepilot.gmgn.rate_limiter import RateLimiter  # noqa: E402
from memepilot.gmgn.read_client import GmgnReadClient  # noqa: E402
from memepilot.logging_utils import redact  # noqa: E402
from memepilot.relay.router import RoutedClient  # noqa: E402
from memepilot.reporting.telegram import notifier_from_env  # noqa: E402
from memepilot.reporting.watchdog import (  # noqa: E402
    BEAT_S,
    NO_CYCLE_ALERT_S,
    Watchdog,
    ping_healthcheck,
    read_heartbeat,
    startup_message,
    write_heartbeat,
)
from memepilot.reporting.weekly import build_weekly, render_markdown, render_telegram  # noqa: E402
from memepilot.strategy.hybrid import profile_config  # noqa: E402
from memepilot.web.server import LiveServer  # noqa: E402
from memepilot.web.snapshot import EventLog, build_snapshot  # noqa: E402

DEMO_KEY = "gmgn_solbscbaseethmonadtron"


ALREADY_RUNNING = 3  # code de sortie lu par run_forever.ps1 (ne pas relancer en boucle)


def port_in_use(host: str, port: int) -> bool:
    if port == 0:
        return False
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return True
    return False


def keep_awake(on: bool) -> bool:
    """Windows : empêche la mise en VEILLE automatique tant que le bot tourne (l'écran peut s'éteindre).

    Ne remplace pas le réglage « fermeture du capot » : voir README. Sans effet hors Windows.
    """
    if sys.platform != "win32":
        return False
    import ctypes

    es_continuous, es_system_required = 0x80000000, 0x00000001
    flags = es_continuous | es_system_required if on else es_continuous
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags)) and on  # type: ignore[attr-defined]


def backoff_interval(interval: float, limited_streak: int, cap: float = 180.0) -> float:
    """Intervalle entre deux cycles : normal, puis ×2, ×4, ×8… après des cycles bloqués d'affilée."""
    if limited_streak <= 0:
        return interval
    return max(interval, min(cap, interval * 2 ** min(limited_streak, 6)))


def load_cached_view(path: Path) -> dict[str, Any] | None:
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data.get("candidates"), list) else None
    except (OSError, ValueError):
        return None


def save_cached_view(path: Path, snap: dict[str, Any]) -> None:
    payload = {"ts": time.time(), "candidates": snap["candidates"], "scan": snap["scan"]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)  # écriture atomique


async def maybe_weekly(engine: PaperEngine, reports_dir: Path, last_path: Path) -> None:
    """Un rapport par semaine (lundi UTC), calculé depuis le ledger et envoyé sur Telegram si configuré."""
    now = datetime.now(UTC)
    week = now.strftime("%G-W%V")
    if last_path.exists() and last_path.read_text(encoding="utf-8").strip() == week:
        return
    if now.weekday() != 0 and last_path.exists():
        return
    t1 = now.timestamp()
    rep = build_weekly(engine.ledger, (now - timedelta(days=7)).timestamp(), t1, engine.limits.initial_capital_usd)
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / f"weekly_{week}.md").write_text(render_markdown(rep), encoding="utf-8")
    await engine.notifier.send(render_telegram(rep))
    last_path.write_text(week, encoding="utf-8")


async def main_async(args: argparse.Namespace) -> int:
    data = ROOT / "data"
    data.mkdir(exist_ok=True)
    db = data / "paper.db"
    # Un seul bot à la fois, vérifié AVANT de toucher à la base (sinon --fresh archiverait la base d'un bot en marche).
    if port_in_use(args.host, args.port):
        print(f"✘ Le port {args.port} est déjà utilisé : un autre MemePilot tourne sans doute déjà "
              f"(ouvre http://127.0.0.1:{args.port}). Ferme l'autre fenêtre, ou choisis --port.")
        return ALREADY_RUNNING
    if (args.fresh or args.reset) and db.exists():
        archive = data / f"paper_{time.strftime('%Y%m%d_%H%M%S')}.db"
        db.rename(archive)
        for suffix in ("-wal", "-shm"):  # fichiers compagnons SQLite : ils suivent la base archivée
            side = db.with_name(db.name + suffix)
            if side.exists():
                side.rename(archive.with_name(archive.name + suffix))
        print(f"Ancien portefeuille papier archivé : {archive.name} — nouveau départ à 50 $.")
        (data / "last_cycle.json").unlink(missing_ok=True)
    if args.reset:
        print("Remise à zéro faite. Relance le bot normalement (.\\scripts\\run_forever.ps1).")
        return 0
    env = load_project_env(ROOT / ".env")
    notifier = notifier_from_env(env)
    if args.offline:
        client: GmgnReadClient = OfflineClient()
        key = "hors ligne (fixtures)"
    else:
        gmgn = GmgnReadClient(args.cli, limiter=RateLimiter(args.plan),
                              env_overrides={"GMGN_API_KEY": DEMO_KEY} if args.demo else {})
        # D-024 : GMGN d'abord, relais gratuit (DexScreener + GeckoTerminal + RugCheck) quand GMGN bloque
        client = RoutedClient(gmgn, mode=args.source)
        key = "démo publique (partagée)" if args.demo else f"personnelle ({args.plan})"
        key += {"auto": " + relais", "relay": " → relais seul", "gmgn": ""}[args.source]
    limits = load_limits(ROOT / "config" / "limits.yaml")
    hybrid = profile_config(args.profile, "paper")
    engine = PaperEngine(client, str(db), limits, notifier=notifier, cycle_cfg=CycleConfig(hybrid=hybrid))
    engine.observe_only = args.observe  # D-041 : laboratoire, aucun achat
    if args.observe:
        key += " · OBSERVATION (aucun achat)"

    log = EventLog()
    view: dict[str, Any] = {"state": None, "cycle": None, "verdicts": {}}
    rev = ROOT / "REVISION"
    version = rev.read_text(encoding="utf-8").strip() if rev.exists() else "local"
    meta: dict[str, Any] = {"version": version, "key": key, "monitor_s": args.monitor, "interval_s": args.interval,
                            "last_cycle_ts": None, "last_monitor_ts": None, "cycle_n": 0,
                            "cycles_ok": 0, "cycles_limited": 0, "limited_streak": 0, "next_cycle_ts": None,
                            "started_ts": time.time()}
    # Dernier cycle COMPLET, conservé sur disque : l'interface n'est jamais vide, même après un redémarrage
    # ou quand la clé de démo est saturée pendant longtemps.
    cache_path = data / "last_cycle.json"
    cached = load_cached_view(cache_path)

    def snapshot() -> dict[str, Any]:
        return build_snapshot(engine, view["state"], view["cycle"], view["verdicts"], meta, log, cached=cached)

    server = LiveServer(snapshot, host=args.host, port=args.port)
    try:
        await server.start()
    except OSError:
        # Port déjà pris : presque toujours un AUTRE MemePilot déjà lancé (deux bots = débit GMGN divisé par deux
        # et bannissements en chaîne sur la clé de démo).
        print(f"✘ Le port {args.port} est déjà utilisé : un autre MemePilot tourne sans doute déjà "
              f"(ouvre http://127.0.0.1:{args.port}). Ferme l'autre fenêtre, ou choisis --port.")
        engine.close()
        return ALREADY_RUNNING
    awake = keep_awake(not args.allow_sleep)
    if awake:
        log.add("info", "mise en veille automatique de Windows suspendue tant que le bot tourne")
    print(f"Interface live : {server.url}  (Ctrl+C pour arrêter)")
    if not args.no_browser:
        webbrowser.open(server.url)
    await notifier.send(f"▶️ MemePilot papier démarré (clé {key}, profil {args.profile}). Aucun ordre réel possible.")
    log.add("info", f"démarrage · clé {key} · profil {args.profile} · "
                    f"scan {args.interval:g} s · surveillance {args.monitor:g} s")

    def record_sells(sells: list[tuple[str, str, Any]]) -> None:
        for sym, reason, pnl in sells:
            log.add("sell", f"VENTE {sym} {pnl:+.2f} $ ({reason})")
            print(f"[{time.strftime('%H:%M:%S')}] vente {sym} {pnl:+.2f} $ ({reason})")

    # D-037 : chien de garde. Arrêt précédent (PC éteint, veille, plantage) signalé au démarrage.
    hb_path = data / "heartbeat.json"
    if not args.offline:
        down = startup_message(read_heartbeat(hb_path), time.time())
        if down:
            log.add("error", down)
            await notifier.send(down)
    write_heartbeat(hb_path, time.time())
    hc_url = env.get("MEMEPILOT_HEALTHCHECK_URL", "")

    async def watchdog_loop() -> None:
        wd = Watchdog(last_beat=time.time())
        while True:
            await asyncio.sleep(BEAT_S)
            try:
                now = time.time()
                for msg in wd.check(now, meta["started_ts"], meta.get("data_cycle_ts"), meta["last_monitor_ts"],
                                    len(engine.ledger.open_positions())):
                    log.add("error" if msg.startswith(("⚠", "🚨")) else "info", msg)
                    await notifier.send(msg)
                write_heartbeat(hb_path, now)
                cycle_ts = meta.get("data_cycle_ts") or 0.0
                if hc_url and now - cycle_ts < NO_CYCLE_ALERT_S:  # signal externe seulement si le bot travaille
                    await asyncio.to_thread(ping_healthcheck, hc_url)
            except Exception as exc:  # noqa: BLE001 — le chien de garde ne doit jamais arrêter le bot
                log.add("error", f"chien de garde : {redact(str(exc))[:120]}")

    async def monitor_loop() -> None:
        """Boucle rapide : seulement les positions ouvertes, toutes les `--monitor` secondes."""
        while True:
            try:
                r = await engine.monitor(args.monitor)
                meta["last_monitor_ts"] = time.time()
                if r.state is not None:
                    view["state"] = r.state
                record_sells(r.sells)
                for sym in r.buys:  # achats confirmés ~5 s après la décision (D-030)
                    log.add("buy", f"ACHAT {sym}")
                    print(f"[{time.strftime('%H:%M:%S')}] achat {sym} (prix confirmé)")
                for e in r.errors:
                    if e.startswith("achat annulé"):
                        log.add("info", e)
            except Exception as exc:  # noqa: BLE001 — la surveillance ne doit jamais s'arrêter
                log.add("error", f"surveillance : {redact(str(exc))[:160]}")
            await asyncio.sleep(args.monitor)

    async def cycle_loop() -> None:
        i, crashes = 0, 0
        while args.cycles == 0 or i < args.cycles:
            i += 1
            frozen = client.limiter.frozen_for
            if frozen > 0:  # gel après un 429 : attendre la fin plutôt que lancer un cycle voué à l'échec
                log.add("error", f"débit limité : reprise dans {frozen:.0f} s")
                await asyncio.sleep(frozen + 1)
            started = time.monotonic()
            try:
                res = await engine.run_cycle()
                crashes = 0
            except Exception as exc:  # noqa: BLE001 — un cycle raté ne doit pas arrêter le service
                crashes += 1
                msg = redact(f"{type(exc).__name__}: {exc}")
                engine.ledger.event(time.time(), "cycle_error", msg[:300])
                log.add("error", f"cycle {i} en erreur : {msg[:160]}")
                print(f"[{time.strftime('%H:%M:%S')}] ✘ cycle {i} en erreur : {msg}")
                if args.verbose:
                    traceback.print_exc()
                if crashes == 3:
                    await notifier.send(f"⚠️ MemePilot papier : 3 cycles en erreur de suite ({msg[:150]})")
            else:
                meta["cycle_n"], meta["last_cycle_ts"] = i, time.time()
                if res.state is not None:
                    view["state"] = res.state
                took = time.monotonic() - started
                # Un cycle interrompu par le débit ne remplace pas le dernier cycle complet à l'écran.
                if res.cycle is not None and not res.cycle.scan.rate_limited and res.cycle.scan.rows_seen > 0:
                    view["cycle"], view["verdicts"] = res.cycle, res.verdicts
                    meta["data_cycle_ts"] = time.time()
                    meta["cycles_ok"] += 1
                    meta["limited_streak"] = 0
                    save_cached_view(cache_path, snapshot())
                    log.add("cycle", f"cycle {i} : {len(res.cycle.scan.survivors)} sûrs, "
                                     f"{len(res.cycle.proposals)} proposition(s), {took:.1f} s "
                                     f"· données {res.data_source}")
                    meta["data_source"] = res.data_source
                else:
                    meta["cycles_limited"] += 1
                    meta["limited_streak"] += 1
                    who = "GMGN et relais tous deux limités" if args.source == "auto" else "limite de débit"
                    log.add("error", f"cycle {i} bloqué : {who} ({meta['limited_streak']} d'affilée)")
                record_sells(res.sells)
                for sym in res.buys:
                    log.add("buy", f"ACHAT papier {sym}")
                for e in res.errors[:4]:
                    # Comportements prévus du relais (alternance des listes, quota de bougies, flux réutilisé) : info
                    expected = any(k in e for k in ("alternance", "quota GeckoTerminal", "réutilisé", "gel en cours"))
                    log.add("info" if expected else "error", redact(e)[:160])
                cap = f", capital {res.state.realized_equity_usd:.2f} $" if res.state else ""
                if meta["limited_streak"]:
                    nxt = backoff_interval(args.interval, meta["limited_streak"])
                    who = "GMGN et relais tous deux limités" if args.source == "auto" else "limite de débit"
                    print(f"[{time.strftime('%H:%M:%S')}] cycle {i} BLOQUÉ ({who}) "
                          f"({meta['limited_streak']} d'affilée) — analyse annulée, prochain essai dans {nxt:.0f} s"
                          f"{cap}. Les positions restent surveillées.")
                else:
                    print(f"[{time.strftime('%H:%M:%S')}] cycle {i} ({took:.1f} s, {res.data_source}) : "
                          f"{len(res.cycle.scan.survivors) if res.cycle else 0} sûrs, "
                          f"{len(res.cycle.proposals) if res.cycle else 0} proposition(s), "
                          f"achats {res.buys or '—'}{cap}")
            await maybe_weekly(engine, data / "reports", data / "last_weekly.txt")
            if args.cycles and i >= args.cycles:
                break
            # Intervalle mesuré de début à début ; recul progressif si la clé est saturée (×2, ×4… plafonné à 180 s)
            # pour ne pas prolonger les bannissements (chaque requête pendant un ban l'allonge de 5 s).
            wait = backoff_interval(args.interval, meta["limited_streak"]) - (time.monotonic() - started)
            meta["next_cycle_ts"] = time.time() + max(1.0, wait)
            await asyncio.sleep(max(1.0, wait))

    mon = asyncio.create_task(monitor_loop())
    dog = asyncio.create_task(watchdog_loop())
    try:
        await cycle_loop()
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\nArrêt demandé — état sauvegardé.")
    finally:
        mon.cancel()
        dog.cancel()
        await asyncio.gather(mon, dog, return_exceptions=True)
        await server.stop()
        keep_awake(False)
        await notifier.send("⏹ MemePilot papier arrêté.")
        engine.close()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Trading papier MemePilot (Phase 5)")
    src = p.add_mutually_exclusive_group()
    src.add_argument("--demo", action="store_true")
    src.add_argument("--offline", action="store_true")
    p.add_argument("--cycles", type=int, default=0, help="0 = boucle continue")
    p.add_argument("--interval", type=float, default=30.0, help="secondes entre deux scans (début à début)")
    p.add_argument("--monitor", type=float, default=2.0,
                   help="secondes entre deux vérifications des positions (D-030 : 2 s)")
    p.add_argument("--plan", default="free", choices=["free", "plus", "pro"], help="plan GMGN de ta clé personnelle")
    p.add_argument("--host", default="127.0.0.1", help="adresse de l'interface (laisser 127.0.0.1)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--source", default="auto", choices=["auto", "gmgn", "relay"],
                   help="auto (défaut) : GMGN puis relais gratuit si GMGN bloque ; "
                        "gmgn : GMGN seul ; relay : relais seul")
    p.add_argument("--observe", action="store_true",
                   help="laboratoire (D-041) : aucun achat, tous les candidats suivis 15 min (scripts\\lab_report.py)")
    p.add_argument("--allow-sleep", action="store_true", help="laisser Windows se mettre en veille (bot en pause)")
    p.add_argument("--fresh", action="store_true", help="archiver la base papier et repartir à 50 $")
    p.add_argument("--reset", action="store_true",
                   help="archiver la base papier (nouveau départ à 50 $) puis quitter, sans lancer le bot")
    p.add_argument("--profile", default="exploration", choices=["exploration", "strict"],
                   help="exploration (défaut, papier seulement) : seuil 55 et 1 confirmation, risque LOW/MEDIUM")
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--cli", default=os.environ.get("GMGN_CLI_PATH", "gmgn-cli"))
    args = p.parse_args()
    if not args.offline:
        # Clé de démo partagée : en dessous de 20 s, les bannissements 429 annulent le gain.
        floor = 20.0 if args.demo else {"free": 15.0, "plus": 8.0, "pro": 5.0}[args.plan]
        if args.interval < floor:
            p.error(f"--interval doit être ≥ {floor:g} s pour cette clé")
        if args.monitor < 2:
            p.error("--monitor doit être ≥ 2 s")
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
