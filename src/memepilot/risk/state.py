"""État de risque et transitions comptables (Phase 4) — pur, immuable, en Decimal.

Toutes les valeurs monétaires sont en USD `Decimal`. Les frais sont TOUJOURS comptés dans les pertes.

Définitions (D-010) :
- exposition ouverte = Σ (coût + frais d'entrée + frais de sortie estimés) des positions ouvertes
  + montants réservés par les ordres non résolus. C'est la perte maximale si tout tombe à zéro.
- pertes du jour = max(0, −P&L réalisé du jour UTC), frais inclus.
- pertes cumulées = max(0, capital initial − plus bas capital réalisé atteint). Un gain après une
  perte ne rend PAS le budget consommé (on retient le pire point).
- LOCK : posé dès que les pertes cumulées atteignent la limite ; persistant ; levé uniquement à la main.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

ZERO = Decimal("0")


class StateError(ValueError):
    """Transition impossible (ex. fermer une position inexistante)."""


@dataclass(frozen=True)
class OpenPosition:
    token: str
    cost_usd: Decimal
    entry_fee_usd: Decimal
    est_exit_fee_usd: Decimal
    opened_day: date

    @property
    def worst_case_loss(self) -> Decimal:
        return self.cost_usd + self.entry_fee_usd + self.est_exit_fee_usd


@dataclass(frozen=True)
class PendingOrder:
    token: str
    reserved_usd: Decimal  # montant + frais estimés, bloqué jusqu'à résolution


@dataclass(frozen=True)
class RiskState:
    day: date
    initial_capital_usd: Decimal
    realized_equity_usd: Decimal  # capital après P&L réalisés (hors positions ouvertes)
    realized_equity_low_usd: Decimal  # plus bas atteint depuis le début
    realized_pnl_today_usd: Decimal = ZERO
    trades_today: int = 0
    positions: tuple[OpenPosition, ...] = ()
    pending: tuple[PendingOrder, ...] = ()
    locked: bool = False
    lock_reason: str = ""
    halted: bool = False
    limits_fingerprint: str = ""
    events: tuple[str, ...] = field(default=(), compare=False)

    # ── lectures ──────────────────────────────────────────────────────────
    @property
    def open_exposure_usd(self) -> Decimal:
        return sum((p.worst_case_loss for p in self.positions), ZERO) + sum(
            (o.reserved_usd for o in self.pending), ZERO
        )

    @property
    def daily_loss_usd(self) -> Decimal:
        return max(ZERO, -self.realized_pnl_today_usd)

    @property
    def cumulative_loss_usd(self) -> Decimal:
        return max(ZERO, self.initial_capital_usd - self.realized_equity_low_usd)

    @property
    def busy_tokens(self) -> set[str]:
        return {p.token for p in self.positions} | {o.token for o in self.pending}

    @property
    def slots_used(self) -> int:
        return len(self.positions) + len(self.pending)


def new_state(initial_capital_usd: Decimal, day: date, limits_fingerprint: str) -> RiskState:
    return RiskState(day=day, initial_capital_usd=initial_capital_usd, realized_equity_usd=initial_capital_usd,
                     realized_equity_low_usd=initial_capital_usd, limits_fingerprint=limits_fingerprint)


def _ev(s: RiskState, msg: str) -> tuple[str, ...]:
    return (*s.events[-49:], msg)


# ── transitions ───────────────────────────────────────────────────────────
def roll_day(s: RiskState, today: date) -> RiskState:
    """Nouveau jour UTC : compteurs journaliers remis à zéro. Le LOCK et le HALT persistent."""
    if today < s.day:
        raise StateError("date antérieure à l'état : horloge incohérente")
    if today == s.day:
        return s
    return replace(s, day=today, realized_pnl_today_usd=ZERO, trades_today=0, events=_ev(s, f"jour {today}"))


def reserve(s: RiskState, token: str, reserved_usd: Decimal) -> RiskState:
    """Un ordre d'achat part : le montant est réservé avant même l'envoi (idempotence, F-31/F-33)."""
    if token in s.busy_tokens:
        raise StateError(f"{token} a déjà une position ou un ordre non résolu")
    return replace(s, pending=(*s.pending, PendingOrder(token, reserved_usd)),
                   events=_ev(s, f"réservé {reserved_usd} $ sur {token}"))


def cancel_reservation(s: RiskState, token: str) -> RiskState:
    """Ordre prouvé échoué/expiré : la réservation est libérée (aucun trade compté)."""
    if token not in {o.token for o in s.pending}:
        raise StateError(f"aucun ordre en attente sur {token}")
    return replace(s, pending=tuple(o for o in s.pending if o.token != token),
                   events=_ev(s, f"réservation annulée sur {token}"))


def fill_buy(s: RiskState, token: str, cost_usd: Decimal, entry_fee_usd: Decimal,
             est_exit_fee_usd: Decimal) -> RiskState:
    """Achat CONFIRMÉ : la réservation devient une position ; un trade est compté."""
    if token not in {o.token for o in s.pending}:
        raise StateError(f"achat confirmé sans réservation sur {token}")
    if min(cost_usd, entry_fee_usd, est_exit_fee_usd) < 0:
        raise StateError("montants négatifs")
    pos = OpenPosition(token, cost_usd, entry_fee_usd, est_exit_fee_usd, s.day)
    return replace(s, pending=tuple(o for o in s.pending if o.token != token), positions=(*s.positions, pos),
                   trades_today=s.trades_today + 1, events=_ev(s, f"achat {token} {cost_usd} $"))


def close_position(s: RiskState, token: str, proceeds_usd: Decimal, exit_fee_usd: Decimal,
                   cumulative_limit_usd: Decimal) -> RiskState:
    """Vente CONFIRMÉE : P&L réalisé = produit − coût − frais d'entrée − frais de sortie."""
    pos = next((p for p in s.positions if p.token == token), None)
    if pos is None:
        raise StateError(f"aucune position ouverte sur {token}")
    if proceeds_usd < 0 or exit_fee_usd < 0:
        raise StateError("montants négatifs")
    pnl = proceeds_usd - pos.cost_usd - pos.entry_fee_usd - exit_fee_usd
    equity = s.realized_equity_usd + pnl
    low = min(s.realized_equity_low_usd, equity)
    out = replace(s, positions=tuple(p for p in s.positions if p.token != token), realized_equity_usd=equity,
                  realized_equity_low_usd=low, realized_pnl_today_usd=s.realized_pnl_today_usd + pnl,
                  events=_ev(s, f"vente {token} P&L {pnl:+.2f} $"))
    if out.cumulative_loss_usd >= cumulative_limit_usd and not out.locked:
        out = replace(out, locked=True, lock_reason=f"perte cumulée {out.cumulative_loss_usd:.2f} $ ≥ "
                      f"{cumulative_limit_usd} $", events=_ev(out, "LOCK : perte cumulée maximale atteinte"))
    return out


def reduce_position(s: RiskState, token: str, fraction: Decimal, proceeds_usd: Decimal, exit_fee_usd: Decimal,
                    cumulative_limit_usd: Decimal) -> RiskState:
    """Vente PARTIELLE confirmée (D-030) : `fraction` du coût et des frais d'entrée est réalisée.

    Pas de nouveau trade compté. Les frais de sortie estimés du reste sont gardés en entier (prudent).
    """
    pos = next((p for p in s.positions if p.token == token), None)
    if pos is None:
        raise StateError(f"aucune position ouverte sur {token}")
    if not (ZERO < fraction < 1) or proceeds_usd < 0 or exit_fee_usd < 0:
        raise StateError("vente partielle invalide")
    part_cost = (pos.cost_usd * fraction).quantize(Decimal("0.01"))
    part_fee = (pos.entry_fee_usd * fraction).quantize(Decimal("0.01"))
    pnl = proceeds_usd - part_cost - part_fee - exit_fee_usd
    rest = replace(pos, cost_usd=pos.cost_usd - part_cost, entry_fee_usd=pos.entry_fee_usd - part_fee)
    equity = s.realized_equity_usd + pnl
    out = replace(s, positions=tuple(rest if p.token == token else p for p in s.positions),
                  realized_equity_usd=equity, realized_equity_low_usd=min(s.realized_equity_low_usd, equity),
                  realized_pnl_today_usd=s.realized_pnl_today_usd + pnl,
                  events=_ev(s, f"vente partielle {token} P&L {pnl:+.2f} $"))
    if out.cumulative_loss_usd >= cumulative_limit_usd and not out.locked:
        out = replace(out, locked=True, lock_reason=f"perte cumulée {out.cumulative_loss_usd:.2f} $ ≥ "
                      f"{cumulative_limit_usd} $", events=_ev(out, "LOCK : perte cumulée maximale atteinte"))
    return out


def set_halt(s: RiskState, halted: bool, reason: str) -> RiskState:
    return replace(s, halted=halted, events=_ev(s, ("HALT : " if halted else "reprise : ") + reason))
