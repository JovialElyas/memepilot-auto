"""Moteur de trading PAPIER (Phase 5) : la boucle complète, sans aucun ordre réel.

Un cycle :
 1. bascule de jour + réconciliation (ledger ↔ état de risque ; ordres interrompus annulés) ;
 2. surveillance des positions ouvertes : prix frais, règles de sortie, vente papier ;
 3. analyse (scan + signaux + stratégie hybride) ;
 4. pour chaque PROPOSE, du meilleur au moins bon : Guardian sur l'état À JOUR, prix frais,
    réservation AVANT l'exécution, achat papier, position ;
 5. instantané d'équité et sauvegarde.
Toute exception d'un cycle est capturée par l'appelant ; l'état est sauvegardé après chaque étape.
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, Decimal

from memepilot.config.limits import Limits
from memepilot.engine.cycle import (
    CycleConfig,
    CycleReport,
    FeedCache,
    analyze_cycle,
    build_request,
    dominant_module,
)
from memepilot.gmgn.models import TokenInfo
from memepilot.gmgn.read_client import GmgnError, GmgnRateLimited, GmgnReadClient
from memepilot.ledger.ledger import PARTIAL_REASON, Ledger, OpenPositionRow
from memepilot.paper.broker import QTY, PaperBroker, PaperRejected
from memepilot.paper.costs import CostModel
from memepilot.paper.exits import CURRENT_EXITS, ExitConfig, check_exit
from memepilot.paper.shadow import LAB_KIND
from memepilot.reporting.telegram import Notifier, NullNotifier
from memepilot.risk.guardian import RiskDecision, budgets, evaluate
from memepilot.risk.state import (
    RiskState,
    cancel_reservation,
    close_position,
    fill_buy,
    reduce_position,
    reserve,
    roll_day,
    set_halt,
)
from memepilot.risk.store import RiskStore
from memepilot.strategy.hybrid import STRICT, Decision, decide

MODE = "paper"
LOSS_COOLDOWN_S = 7200.0  # D-027 : pas de rachat d'un token pendant 2 h après une perte dessus
CONTROL_MAX_TICK_AGE_S = 20.0  # D-027 : une position est « sous contrôle » si son prix a moins de 20 s…
CONTROL_MAX_LOSS = Decimal("0.10")  # … et si elle ne perd pas plus de 10 %
POST_EXIT_WATCH_S = 1800.0  # suivi du prix après la vente, pour le rejeu des règles de sortie (D-026)
PRICE_JUMP_CONFIRM_RATIO = Decimal("0.5")  # chute > 50 % en un contrôle : à confirmer avant toute vente
# D-030 : réactivité. Achat confirmé ~5 s après la décision si le prix est resté calme ; grosse chute revérifiée
# ~1 s plus tard (au lieu du contrôle suivant) ; vente papier au pire des deux prix vus à ~1 s d'écart (délai réel).
ENTRY_CONFIRM_S = 5.0
ENTRY_MAX_DROP = Decimal("0.05")  # chute ≥ 5 % pendant la confirmation → achat annulé
ENTRY_MAX_RISE = Decimal("0.08")  # hausse ≥ 8 % pendant la confirmation → pic en cours, achat annulé
ENTRY_TIMEOUT_S = 30.0  # confirmation impossible (débit limité…) au-delà → achat annulé
CRASH_RECHECK_S = 1.2  # > cache prix du relais (1 s)
SELL_LATENCY_S = 1.0
POST_EXIT_TICK_S = 5.0  # le suivi après vente reste à 5 s (sert au rejeu, pas à décider)
# D-037 : suivi des tokens REFUSÉS pendant 30 min (prix toutes les 15 s, relais d'abord) pour juger les refus.
SHADOW_WATCH_S = 1800.0
SHADOW_TICK_S = 15.0
SHADOW_MAX = 20  # tokens suivis en même temps
SHADOW_QUOTES_PER_TICK = 2  # la surveillance des positions reste prioritaire
# D-041 : LABORATOIRE (mode observation). Aucun achat ; TOUS les candidats analysés sont suivis 15 min, prix
# toutes les 5 s par lots DexScreener (30 tokens par requête), avec leurs caractéristiques au moment de la décision.
# D-043 (H2, trend follower) : suivi étendu à 4 h — toutes les 5 s pendant 15 min, puis toutes les minutes.
LAB_WATCH_S = 4 * 3600.0
LAB_FAST_S = 900.0
LAB_TICK_S = 5.0
LAB_SLOW_TICK_S = 60.0
LAB_MAX = 600
DEFAULT_CYCLE_S = 60.0  # durée de cycle pour laquelle ExitConfig.max_missing_cycles a été calibré
ZERO = Decimal(0)


@dataclass
class PaperCycleResult:
    cycle: CycleReport | None = None
    buys: list[str] = field(default_factory=list)
    sells: list[tuple[str, str, Decimal]] = field(default_factory=list)  # (symbole, raison, P&L)
    verdicts: dict[str, RiskDecision] = field(default_factory=dict)
    unrealized_usd: Decimal = ZERO
    errors: list[str] = field(default_factory=list)
    state: RiskState | None = None
    data_source: str = "gmgn"  # gmgn | relais | mixte — source des données de l'analyse (D-024)


@dataclass
class _PendingEntry:
    """Achat approuvé, en attente de la confirmation de prix (D-030). Sa réservation bloque déjà le budget."""

    token: str
    symbol: str
    decision: object
    size_usd: Decimal
    price: Decimal
    ts: float
    decision_id: int
    order_id: int
    strategy: str
    strict_ok: bool
    data_source: str
    cand_liquidity: float | None


def _cycle_source(before: dict[str, int], after: dict[str, int] | None) -> str:
    if not after:
        return "gmgn"
    g = after.get("gmgn", 0) - before.get("gmgn", 0)
    r = after.get("relais", 0) - before.get("relais", 0)
    return "mixte" if g and r else ("relais" if r else "gmgn")


def _dec(x: float | None, q: str = "0.01") -> Decimal | None:
    return None if x is None else Decimal(str(x)).quantize(Decimal(q)) if q != "raw" else Decimal(str(x))


class PaperEngine:
    def __init__(self, client: GmgnReadClient, db_path: str, limits: Limits, costs: CostModel = CostModel(),  # noqa: B008
                 exits: ExitConfig = CURRENT_EXITS, notifier: Notifier | None = None,
                 cycle_cfg: CycleConfig = CycleConfig(), clock: Callable[[], float] = time.time) -> None:  # noqa: B008
        self.client = client
        self.ledger = Ledger(db_path)
        self.store = RiskStore(db_path)
        self.limits = limits
        self.costs = costs
        self.broker = PaperBroker(costs)
        self.exits = exits
        self.notifier: Notifier = notifier or NullNotifier()
        self.cycle_cfg = cycle_cfg
        self.clock = clock
        self.feed_cache = FeedCache()
        self.lock = asyncio.Lock()  # le cycle d'analyse et la boucle rapide ne s'exécutent jamais en même temps
        self.last_prices: dict[str, Decimal] = {}
        self._suspect: dict[str, int] = {}
        # Après une vente, le prix du token reste enregistré 30 min : le rejeu peut ainsi juger aussi des règles
        # de sortie PLUS lâches (que se serait-il passé si on avait gardé ?). token → (opened_ts, fin du suivi)
        self._post_watch: dict[str, tuple[float, float]] = {}
        self._last_tick_ts: dict[str, float] = {}  # dernier prix confirmé de chaque position ouverte (D-027)
        self._post_last: dict[str, float] = {}
        self._entries: dict[str, _PendingEntry] = {}  # achats en cours de confirmation (D-030)
        self._shadow: dict[str, tuple[int, float, float]] = {}  # token → (id, fin du suivi, dernier relevé)
        self.shadow_max = SHADOW_MAX
        self.observe_only = False  # D-041 : True = laboratoire, aucun achat
        self._lab: dict[str, tuple[int, float]] = {}  # token → (id, fin du suivi)
        self._lab_seen: dict[str, float] = {}  # token → dernier relevé
        self._lab_start: dict[str, float] = {}  # token → début du suivi
        self._resume_lab()
        self._lab_last = 0.0
        self.entry_confirm_s = ENTRY_CONFIRM_S
        self.crash_recheck_s: float | None = CRASH_RECHECK_S
        self.sell_latency_s: float | None = SELL_LATENCY_S
        self.last_unrealized = ZERO

    def close(self) -> None:
        self.ledger.close()
        self.store.close()

    # ── utilitaires ───────────────────────────────────────────────────────
    @staticmethod
    def _day(ts: float) -> date:
        return datetime.fromtimestamp(ts, UTC).date()

    async def _quote(self, token: str) -> tuple[Decimal | None, Decimal | None]:
        """Prix et liquidité frais (token info, poids 1). Token inconnu ou erreur → (None, None).

        Une limitation de débit est propagée (GmgnRateLimited) : ce n'est PAS un prix manquant.
        """
        try:
            quote = getattr(self.client, "quote", None)  # D-036 : relais d'abord pour les prix des positions
            res = await (quote(token) if quote else self.client.call(("token", "info"), chain="sol", address=token))
        except GmgnRateLimited:
            raise
        except GmgnError:
            return None, None
        if not isinstance(res.data, dict):
            return None, None
        info = TokenInfo.from_api(res.data)
        if info.is_unknown or not info.price_usd:
            return None, None
        return _dec(info.price_usd, "raw"), _dec(info.liquidity)

    def cash(self, s: RiskState) -> Decimal:
        return s.realized_equity_usd - sum((p.cost_usd + p.entry_fee_usd for p in s.positions), ZERO)

    def ledger_cash(self) -> Decimal:
        open_cost = sum((p.cost_usd + p.entry_fee_usd for p in self.ledger.open_positions()), ZERO)
        return self.limits.initial_capital_usd + self.ledger.realized_pnl_total() - open_cost

    def _save(self, s: RiskState) -> None:
        # Un HALT posé à la main (risk_admin.py) pendant un cycle ne doit pas être écrasé :
        # le moteur ne lève jamais un HALT lui-même, seule la commande `resume` le fait.
        stored = self.store.load_or_init(self.limits, MODE, s.day)
        if stored.halted and not s.halted:
            s = set_halt(s, True, "HALT manuel conservé")
        self.store.save(s, MODE)

    def reconcile(self, s: RiskState, now: float) -> RiskState:
        """Au démarrage de chaque cycle : un ordre papier non résolu (crash) est annulé ; un désaccord → HALT."""
        for o in s.pending:
            if o.token in self._entries:  # achat en cours de confirmation (D-030) : pas interrompu
                continue
            s = cancel_reservation(s, o.token)
            self.ledger.event(now, "reconcile", f"réservation interrompue annulée : {o.token}")
        self.ledger.fail_interrupted_orders({e.order_id for e in self._entries.values()})
        ledger_tokens = {p.token for p in self.ledger.open_positions()}
        state_tokens = {p.token for p in s.positions}
        if ledger_tokens != state_tokens and not s.halted:
            s = set_halt(s, True, "désaccord ledger / état de risque")
            self.ledger.event(now, "halt", f"ledger={sorted(ledger_tokens)} état={sorted(state_tokens)}")
        return s

    # ── cycle ─────────────────────────────────────────────────────────────
    async def _watch_exits(self, s: RiskState, now: float, out: PaperCycleResult,
                           exits: ExitConfig) -> tuple[RiskState, Decimal]:
        """Vérifie chaque position ouverte (prix frais) et vend en papier si une règle de sortie se déclenche."""
        unrealized = ZERO
        for pos in self.ledger.open_positions():
            try:
                price, liq = await self._quote(pos.token)
            except GmgnRateLimited:
                out.errors.append(f"surveillance {pos.symbol or pos.token[:6]} : débit limité, ignoré ce tour")
                continue
            self.ledger.record_tick(pos.token, pos.opened_ts, now, price, liq)
            # Garde-fou « prix aberrant » (constaté le 2026-11-01 : pair DexScreener sans liquidité → −100 % fictif).
            # Une chute > 50 % depuis le dernier prix connu doit être CONFIRMÉE avant d'agir. D-030 : on revérifie
            # ~1 s plus tard au lieu d'attendre le contrôle suivant (en vrai krach, chaque seconde compte).
            ref = self.last_prices.get(pos.token, pos.entry_price)
            if price is not None and ref > 0 and price < ref * PRICE_JUMP_CONFIRM_RATIO:
                seen = self._suspect.get(pos.token, 0) + 1
                self._suspect[pos.token] = seen
                again = await self._requote(pos.token, self.crash_recheck_s) if seen < 2 else None
                if seen < 2 and (again is None or again[0] is None):
                    out.errors.append(f"prix suspect {pos.symbol or pos.token[:6]} : {price:.3g} contre {ref:.3g} "
                                      f"({price / ref - 1:+.0%}) — confirmation au prochain contrôle")
                    continue
                if again is not None and again[0] is not None:
                    price, liq = again[0], again[1]
                    self.ledger.record_tick(pos.token, pos.opened_ts, now, price, liq)
                    if price >= ref * PRICE_JUMP_CONFIRM_RATIO:
                        out.errors.append(f"prix aberrant ignoré pour {pos.symbol or pos.token[:6]} (revérifié)")
                self._suspect.pop(pos.token, None)
            else:
                self._suspect.pop(pos.token, None)
            chk = check_exit(pos, price, liq, now, exits, partial_done=pos.partial_done)
            if chk.partial and price is not None:
                s = await self._sell_part(s, pos, price, liq, now, out, exits)
            if chk.reason is None:
                self.ledger.track_position(pos.token, chk.peak_price, chk.missing_cycles)
                if price is not None:
                    cur = pos
                    if chk.partial:  # la position vient d'être réduite : relire ce qu'il en reste
                        cur = next((r for r in self.ledger.open_positions() if r.token == pos.token), pos)
                    unrealized += cur.qty * price - cur.cost_usd
                    self.last_prices[pos.token] = price
                    self._last_tick_ts[pos.token] = now
                continue
            if price is not None:  # délai d'une vraie vente : on exécute au pire des deux prix (D-030)
                price, liq = await self._exec_quote(pos.token, price, liq)
            fill = self.broker.sell(pos.token, pos.qty, price, liq)
            oid = self.ledger.create_order(f"sell:{pos.token}:{pos.opened_ts}", now, pos.token, "sell",
                                           fill.gross_usd, None)
            self.ledger.record_fill(oid, now, fill)
            self.ledger.set_order_state(oid, "CONFIRMED")
            was_locked = s.locked
            s = close_position(s, pos.token, fill.gross_usd, fill.fee_usd, self.limits.maximum_cumulative_loss_usd)
            pnl = fill.gross_usd - pos.cost_usd - pos.entry_fee_usd - fill.fee_usd
            self.ledger.close_position(now, pos.token, fill, pnl, chk.reason)
            self._save(s)
            self.last_prices.pop(pos.token, None)
            self._post_watch[pos.token] = (pos.opened_ts, now + POST_EXIT_WATCH_S)
            out.sells.append((pos.symbol or pos.token[:6], chk.reason, pnl))
            await self.notifier.send(f"{'🟢' if pnl >= 0 else '🔴'} Vente papier {pos.symbol or pos.token[:6]} : "
                                     f"{pnl:+.2f} $ ({chk.reason})")
            if s.locked and not was_locked:
                self.ledger.event(now, "lock", s.lock_reason)
                await self.notifier.send(f"🔒 LOCK : {s.lock_reason}. Plus aucun achat jusqu'au déverrouillage manuel.")
        self.last_unrealized = unrealized
        return s, unrealized

    async def _requote(self, token: str, delay_s: float | None) -> tuple[Decimal | None, Decimal | None] | None:
        """Nouveau prix après `delay_s` secondes. None si désactivé ou si la source est limitée."""
        if delay_s is None:
            return None
        await asyncio.sleep(delay_s)
        try:
            return await self._quote(token)
        except GmgnError:
            return None

    async def _exec_quote(self, token: str, price: Decimal,
                          liq: Decimal | None) -> tuple[Decimal, Decimal | None]:
        """Prix d'exécution papier d'une vente : le pire entre le prix déclencheur et celui vu ~1 s après."""
        again = await self._requote(token, self.sell_latency_s)
        if again is None:
            return price, liq
        p2, l2 = again
        if p2 is None:
            return price, liq
        return (p2, l2 if l2 is not None else liq) if p2 < price else (price, liq)

    async def _sell_part(self, s: RiskState, pos: OpenPositionRow, price: Decimal, liq: Decimal | None,
                         now: float, out: PaperCycleResult, exits: ExitConfig) -> RiskState:
        """Prise de profit partielle (D-030) : vend `partial_fraction` de la position, le reste continue."""
        frac = exits.partial_fraction
        qty = (pos.qty * frac).quantize(QTY, rounding=ROUND_DOWN)
        mid, l2 = await self._exec_quote(pos.token, price, liq)
        fill = self.broker.sell(pos.token, qty, mid, l2)
        oid = self.ledger.create_order(f"sell-part:{pos.token}:{pos.opened_ts}", now, pos.token, "sell",
                                       fill.gross_usd, None)
        self.ledger.record_fill(oid, now, fill)
        self.ledger.set_order_state(oid, "CONFIRMED")
        part_cost = (pos.cost_usd * frac).quantize(Decimal("0.01"))
        part_fee = (pos.entry_fee_usd * frac).quantize(Decimal("0.01"))
        pnl = fill.gross_usd - part_cost - part_fee - fill.fee_usd
        s = reduce_position(s, pos.token, frac, fill.gross_usd, fill.fee_usd, self.limits.maximum_cumulative_loss_usd)
        reason = f"{PARTIAL_REASON} ({mid / pos.entry_price - 1:+.0%}, {frac:.0%} vendu)"
        self.ledger.partial_close(now, pos.token, fill, part_cost, part_fee, pnl, reason)
        self._save(s)
        sym = pos.symbol or pos.token[:6]
        out.sells.append((sym, reason, pnl))
        await self.notifier.send(f"💰 Vente partielle papier {sym} : {pnl:+.2f} $ encaissés ({reason}). "
                                 "Le reste continue, protégé par le trailing.")
        return s

    async def _confirm_entries(self, s: RiskState, now: float, out: PaperCycleResult) -> RiskState:
        """Achats approuvés il y a ≥ 5 s : exécutés si le prix est resté calme, sinon annulés (D-030)."""
        for tok, e in list(self._entries.items()):
            if now - e.ts < self.entry_confirm_s:
                continue
            del self._entries[tok]
            why: str | None = None
            price2: Decimal | None = None
            liq2: Decimal | None = None
            if s.locked or s.halted:
                why = "blocage (LOCK ou HALT) pendant la confirmation"
            elif e.price <= 0:
                why = "prix indisponible à la décision"
            elif now - e.ts > ENTRY_TIMEOUT_S:
                why = "confirmation impossible à temps"
            else:
                try:
                    price2, liq2 = await self._quote(tok)
                except GmgnRateLimited:
                    self._entries[tok] = e  # on réessaie au prochain contrôle (jusqu'au délai maximal)
                    continue
                if price2 is None:
                    why = "prix indisponible à la confirmation"
                else:
                    chg = price2 / e.price - 1
                    if chg <= -ENTRY_MAX_DROP:
                        why = f"chute pendant la confirmation ({chg:+.1%} en {now - e.ts:.0f} s)"
                    elif chg >= ENTRY_MAX_RISE:
                        why = f"s'envole encore pendant la confirmation ({chg:+.1%}) : pic"
            fill = None
            if why is None:
                try:
                    fill = self.broker.buy(tok, e.size_usd, price2, liq2)
                except PaperRejected as exc:
                    why = str(exc)
            if fill is None:
                s = cancel_reservation(s, tok)
                self.ledger.set_order_state(e.order_id, "CANCELLED", why)
                self.ledger.event(now, "entry_cancel", f"{e.symbol} : {why}")
                out.errors.append(f"achat annulé {e.symbol} : {why}")
                self._save(s)
                self._shadow_add(tok, e.symbol, "achat annulé", why or "annulé", price2 or e.price, liq2,
                                 getattr(e.decision, "score", None), now)
                continue
            self.ledger.record_fill(e.order_id, now, fill)
            self.ledger.set_order_state(e.order_id, "CONFIRMED")
            fees_est = self.costs.guardian_fee_estimate(self.limits.max_position_usd)
            s = fill_buy(s, tok, fill.gross_usd, fill.fee_usd, max(ZERO, fees_est - fill.fee_usd))
            d = e.decision
            self.ledger.open_position(now, tok, e.symbol, e.strategy, fill, liq2, e.decision_id,
                                      getattr(d, "profile", "strict"), e.strict_ok, e.data_source)
            self._save(s)
            out.buys.append(e.symbol)
            profile = getattr(d, "profile", "strict")
            tag = "" if profile == "strict" else (" · exploration" + (" (validé strict)" if e.strict_ok else ""))
            liq_txt = f", liq {int(e.cand_liquidity)} $" if e.cand_liquidity else ""
            await self.notifier.send(f"🛒 Achat papier {e.symbol} : {fill.gross_usd} $ "
                                     f"(score {getattr(d, 'score', 0):.0f}, {e.strategy}, "
                                     f"risque {getattr(d, 'risk_class', '?')}{liq_txt}){tag}"
                                     + ("" if e.data_source == "gmgn" else f" · données {e.data_source}"))
        return s

    async def monitor(self, interval_s: float) -> PaperCycleResult:
        """Boucle rapide : surveille SEULEMENT les positions ouvertes (1 appel de poids 1 par position).

        Le seuil « prix absent » est ramené à une durée (≈ 5 min) pour ne pas vendre à 0 après 25 s.
        """
        out = PaperCycleResult()
        async with self.lock:
            now = self.clock()
            today = self._day(now)
            s = roll_day(self.store.load_or_init(self.limits, MODE, today), today)
            await self._watch_after_exit(now)
            await self._watch_shadow(now)
            await self._watch_lab(now)
            if not self.ledger.open_positions() and not self._entries:
                out.state = s
                return out
            misses = max(self.exits.max_missing_cycles,
                         math.ceil(self.exits.max_missing_cycles * DEFAULT_CYCLE_S / max(interval_s, 1.0)))
            s, out.unrealized_usd = await self._watch_exits(s, now, out, replace(self.exits, max_missing_cycles=misses))
            s = await self._confirm_entries(s, self.clock(), out)
            self._save(s)
            out.state = s
        return out

    def _pre_buy_block(self, token: str, now: float) -> str | None:
        """Règles d'achat ajoutées par D-027, AVANT le Guardian (qui garde son veto et ses limites).

        1. Pause de 2 h sur un token après une perte (cas niketyson, racheté plusieurs fois).
        2. Position supplémentaire seulement si le bot garde le contrôle de celles déjà ouvertes : chacune a un prix
           observé il y a moins de 20 s, et aucune ne perd plus de 10 %.
        """
        last_loss = self.ledger.last_loss_ts(token)
        if last_loss is not None and now - last_loss < LOSS_COOLDOWN_S:
            return f"pause après une perte sur ce token (encore {(LOSS_COOLDOWN_S - (now - last_loss)) / 60:.0f} min)"
        for pos in self.ledger.open_positions():
            seen = self._last_tick_ts.get(pos.token)
            last = self.last_prices.get(pos.token)
            if seen is None or now - seen > CONTROL_MAX_TICK_AGE_S or last is None:
                return f"contrôle insuffisant : prix de {pos.symbol or pos.token[:6]} pas encore confirmé"
            if last / pos.entry_price - 1 < -CONTROL_MAX_LOSS:
                return f"contrôle insuffisant : {pos.symbol or pos.token[:6]} perd {last / pos.entry_price - 1:.0%}"
        return None

    def _shadow_add(self, token: str, symbol: str | None, kind: str, reason: str, price: Decimal | None,
                    liq: Decimal | None, score: float | None, now: float) -> None:
        """Commence le suivi d'un token refusé (un seul suivi à la fois par token, plafonné à `shadow_max`)."""
        # Sans prix au moment du refus, le premier relevé (~15 s plus tard) sert de prix de référence.
        if token in self._shadow or len(self._shadow) >= self.shadow_max:
            return
        if any(p.token == token for p in self.ledger.open_positions()):
            return
        sid = self.ledger.add_shadow(token, symbol, kind, reason, now, price if price and price > 0 else None, liq,
                                     score)
        self._shadow[token] = (sid, now + SHADOW_WATCH_S, now - SHADOW_TICK_S if price is None else now)

    async def _watch_shadow(self, now: float) -> None:
        """Prix des tokens refusés, au plus 2 relevés par contrôle (observation seulement, jamais bloquant)."""
        done = 0
        for token, (sid, until, last) in sorted(self._shadow.items(), key=lambda kv: kv[1][2]):
            if now > until:
                del self._shadow[token]
                continue
            if done >= SHADOW_QUOTES_PER_TICK or now - last < SHADOW_TICK_S:
                continue
            done += 1
            self._shadow[token] = (sid, until, now)
            try:
                price, liq = await self._quote(token)
            except GmgnError:
                continue
            self.ledger.record_shadow_tick(sid, now, price, liq)

    def _lab_add(self, d: object, c: object | None, now: float) -> None:
        token = getattr(d, "address", "")
        if not token or token in self._lab or len(self._lab) >= LAB_MAX:
            return
        sig = {s.module: {"score": s.score, "confidence": s.confidence, "vetoes": list(s.vetoes)}
               for s in getattr(d, "signals", ())}
        cand = {k: getattr(c, k, None) for k in ("liquidity", "market_cap", "age_min", "chg_5m", "chg_1h",
                                                  "buy_ratio", "smart_degen", "renowned", "rug_ratio", "top10",
                                                  "bundler", "sniper_hold", "fresh_wallet_rate", "progress")}
        feats = {"score": getattr(d, "score", None), "decision": str(getattr(d, "decision", "")),
                 "risk": str(getattr(d, "risk_class", "")), "signals": sig, "candidate": cand,
                 "sources": sorted(getattr(c, "sources", set()) or [])}
        price = getattr(c, "price", None)
        sid = self.ledger.add_shadow(token, getattr(d, "symbol", None), LAB_KIND,
                                     (getattr(d, "reasons", ()) or ("",))[0], now,
                                     _dec(price, "raw") if price else None, _dec(getattr(c, "liquidity", None)),
                                     getattr(d, "score", None), feats)
        self._lab[token] = (sid, now + LAB_WATCH_S)
        self._lab_start[token] = now

    def _resume_lab(self) -> None:
        """Après un redémarrage, reprend le suivi des candidats encore dans leur fenêtre de 4 h (D-043)."""
        now = self.clock()
        for sh in self.ledger.shadows(now - LAB_WATCH_S, now + 1, kind=LAB_KIND):
            if len(self._lab) < LAB_MAX:
                self._lab[sh["token"]] = (sh["id"], sh["ts"] + LAB_WATCH_S)
                self._lab_start[sh["token"]] = sh["ts"]

    async def _watch_lab(self, now: float) -> None:
        if not self._lab or now - self._lab_last < LAB_TICK_S:
            return
        self._lab_last = now
        for tok, (_sid, until) in list(self._lab.items()):
            if now > until:
                del self._lab[tok]
                self._lab_seen.pop(tok, None)
                self._lab_start.pop(tok, None)
        # Jeunes suivis (< 15 min) : à chaque passage (5 s) ; plus anciens : une fois par minute.
        tokens = [t for t in self._lab
                  if now - self._lab_start.get(t, now) < LAB_FAST_S
                  or now - self._lab_seen.get(t, 0.0) >= LAB_SLOW_TICK_S]
        for t in tokens:
            self._lab_seen[t] = now
        if not tokens:
            return
        batch = getattr(self.client, "batch_prices", None)
        if batch is not None:
            got = await batch(tokens)
            for tok in tokens:
                if tok in got:
                    p, liq = got[tok]
                    self.ledger.record_shadow_tick(self._lab[tok][0], now, _dec(p, "raw") if p else None,
                                                   _dec(liq))
            return
        for tok in tokens[:5]:  # client sans prix groupés (tests, GMGN seul) : quelques relevés seulement
            try:
                price, liq = await self._quote(tok)
            except GmgnError:
                continue
            self.ledger.record_shadow_tick(self._lab[tok][0], now, price, liq)

    async def _watch_after_exit(self, now: float) -> None:
        for token, (opened_ts, until) in list(self._post_watch.items()):
            if now > until:
                del self._post_watch[token]
                self._post_last.pop(token, None)
                continue
            if now - self._post_last.get(token, 0.0) < POST_EXIT_TICK_S:
                continue
            self._post_last[token] = now
            try:
                price, liq = await self._quote(token)
            except GmgnError:
                continue  # suivi d'observation seulement : jamais bloquant
            self.ledger.record_tick(token, opened_ts, now, price, liq)

    async def run_cycle(self) -> PaperCycleResult:
        """Le verrou n'est PAS tenu pendant l'analyse (7 à 40 s de réseau) : la surveillance des positions continue
        toutes les 2 s pendant un scan (D-034 ; avant, elle était aveugle jusqu'à ~1 min à chaque scan)."""
        out = PaperCycleResult()
        async with self.lock:
            await self._cycle_start(out)
        before = dict(getattr(self.client, "source_counts", {}))
        rep = await analyze_cycle(self.client, self.cycle_cfg, self.feed_cache)
        out.data_source = _cycle_source(before, getattr(self.client, "source_counts", None))
        out.cycle = rep
        out.errors += rep.errors + rep.scan.source_errors
        async with self.lock:
            return await self._cycle_buys(out, rep)

    async def _cycle_start(self, out: PaperCycleResult) -> None:
        now = self.clock()
        today = self._day(now)
        s = roll_day(self.store.load_or_init(self.limits, MODE, today), today)
        was_halted = s.halted
        s = self.reconcile(s, now)
        if s.halted and not was_halted:
            await self.notifier.send("⛔ MemePilot (papier) : HALT — désaccord entre le ledger et l'état de risque.")
        self._save(s)
        # 2. Surveillance et sorties
        s, out.unrealized_usd = await self._watch_exits(s, now, out, self.exits)
        self._save(s)

    async def _cycle_buys(self, out: PaperCycleResult, rep: CycleReport) -> PaperCycleResult:
        # L'état est relu : la surveillance a pu vendre pendant l'analyse.
        now = self.clock()
        today = self._day(now)
        cycle_id = uuid.uuid4().hex[:12]  # identifiant de tentative : une clé d'idempotence par ordre logique
        s = roll_day(self.store.load_or_init(self.limits, MODE, today), today)
        unrealized = self.last_unrealized

        # 4. Achats papier, du meilleur score au moins bon, Guardian sur l'état à jour
        fees_est = self.costs.guardian_fee_estimate(self.limits.max_position_usd)
        survivors = {c.address: c for c in rep.scan.survivors}
        if self.observe_only:  # D-041 : laboratoire, on note tout et on n'achète rien
            for d in sorted(rep.decisions, key=lambda x: -x.score):
                self._lab_add(d, survivors.get(d.address), now)
        for d in sorted([] if self.observe_only else rep.proposals, key=lambda x: -x.score):
            block = self._pre_buy_block(d.address, now)
            if block:
                b_day, b_cum = budgets(s, self.limits)
                verdict = RiskDecision(False, ZERO, (block,), b_day, b_cum)
            else:
                verdict = evaluate(build_request(rep, d, fees_est, self.cash(s), self.ledger_cash()), s, self.limits,
                                   today)
            out.verdicts[d.address] = verdict
            strategy = dominant_module(d)
            dec_id = self.ledger.record_decision(now, d.address, d.symbol, d.score, str(d.risk_class), str(d.decision),
                                                 strategy, list(d.reasons), d.to_dict()["signals"], verdict.to_dict())
            if not verdict.approved:
                c0 = survivors.get(d.address)
                first = verdict.reasons[0] if verdict.reasons else ""
                # D-039 : « places pleines » à part, pour mesurer ce que coûte la limite de 2 positions
                kind = "refus contrôle" if block else ("places pleines" if "position(s)/ordre(s)" in first
                                                       else "refus Guardian")
                self._shadow_add(d.address, d.symbol, kind, verdict.reasons[0] if verdict.reasons else "",
                                 _dec(c0.price, "raw") if c0 else None, _dec(c0.liquidity) if c0 else None,
                                 d.score, now)
                continue
            price, _liq = await self._quote(d.address)
            try:
                oid = self.ledger.create_order(f"buy:{d.address}:{cycle_id}", now, d.address, "buy",
                                               verdict.size_usd, dec_id)
            except sqlite3.IntegrityError:
                self.ledger.event(now, "duplicate", f"ordre déjà créé pour {d.address}")
                continue
            s = reserve(s, d.address, verdict.size_usd + fees_est)
            self._save(s)
            c = survivors.get(d.address)
            # Le profil strict aurait-il pris ce trade ? (sépare les résultats d'exploration, D-021)
            strict_ok = c is not None and decide(c, d.signals, STRICT).decision is Decision.PROPOSE
            # D-030 : l'achat n'est exécuté qu'après ~5 s, si le prix est resté calme (voir _confirm_entries).
            self._entries[d.address] = _PendingEntry(
                d.address, d.symbol or d.address[:6], d, verdict.size_usd, price or ZERO, now, dec_id, oid,
                strategy, strict_ok, out.data_source, c.liquidity if c else None)
        s = await self._confirm_entries(s, self.clock(), out)

        # Décisions notables non proposées : journalisées pour l'analyse (sans verdict)
        for d in rep.decisions:
            if d.decision is Decision.NO_TRADE and d.score >= 50:
                self.ledger.record_decision(now, d.address, d.symbol, d.score, str(d.risk_class), str(d.decision),
                                            dominant_module(d), list(d.reasons), d.to_dict()["signals"], None)
        # D-037 : les refus « pic en cours / achat de sommet » (D-029) et les candidats juste sous le seuil
        for d in sorted(rep.decisions, key=lambda x: -x.score):
            if d.decision is not Decision.NO_TRADE:
                continue
            c0 = survivors.get(d.address)
            vetoes = [v for sig in d.signals for v in sig.vetoes]
            spike = next((v for v in vetoes if "pic en cours" in v or "achat de sommet" in v), None)
            if spike:
                kind, why = "refus pic", spike
            elif d.score >= 45 and not vetoes:
                kind, why = "sous le seuil", d.reasons[0] if d.reasons else f"score {d.score:.0f}"
            else:
                continue
            self._shadow_add(d.address, d.symbol, kind, why, _dec(c0.price, "raw") if c0 else None,
                             _dec(c0.liquidity) if c0 else None, d.score, now)

        # 5. Équité
        self.ledger.snapshot_equity(now, s.realized_equity_usd, unrealized, s.open_exposure_usd)
        self._save(s)
        out.state = s
        return out
