"""Bilan des refus (D-037) : « et si on avait acheté ? » sur les tokens que le bot a refusés.

Chaque refus suivi (achat annulé à la confirmation, refus du Guardian ou du contrôle, refus « pic en cours »,
candidat juste sous le seuil) est rejoué avec les règles de sortie ACTUELLES sur ses vrais prix des 30 min
suivantes, avec une mise et des frais réalistes. Un refus est UTILE si l'achat aurait perdu de l'argent.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from memepilot.ledger.ledger import Ledger
from memepilot.paper.costs import CostModel
from memepilot.paper.exits import CURRENT_EXITS, ExitConfig
from memepilot.paper.replay import replay_position

ZERO = Decimal(0)
LAB_KIND = "labo"


@dataclass
class KindStats:
    kind: str
    count: int = 0
    would_win: int = 0
    would_pnl: Decimal = ZERO
    examples: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.count == 0:
            return "—"
        if self.would_pnl < 0:
            return f"UTILE : ces refus ont évité {-self.would_pnl:.2f} $ de pertes"
        return f"COÛTEUX : ces refus ont fait manquer {self.would_pnl:.2f} $"


def evaluate_shadows(ledger: Ledger, t0: float = 0.0, t1: float = 1e12, cfg: ExitConfig = CURRENT_EXITS,
                     stake: Decimal = Decimal("5"), costs: CostModel = CostModel()) -> list[KindStats]:  # noqa: B008
    stats: dict[str, KindStats] = defaultdict(lambda: KindStats(""))
    for sh in ledger.shadows(t0, t1):
        if sh["kind"] == LAB_KIND:  # le laboratoire (D-041) a son propre bilan : scripts/lab_report.py
            continue
        # Un relevé sans prix (token absent du relais) n'est PAS un prix nul : il est ignoré (D-040 ; avant,
        # 5 relevés vides d'affilée simulaient une vente à 0 et faussaient le bilan).
        ticks = [t for t in ledger.shadow_ticks(sh["id"]) if t[1] is not None and t[1] > 0]
        ref = sh["price"]
        if ref is None:  # pas de prix au moment du refus : le premier relevé sert de référence
            first = next((i for i, t in enumerate(ticks) if t[1]), None)
            if first is None:
                continue
            ref, ticks = ticks[first][1], ticks[first + 1:]
        if not ticks or not ref:
            continue
        pos = {"token": sh["token"], "symbol": sh["symbol"], "opened_ts": sh["ts"], "entry_price": ref,
               "entry_liquidity": sh["liquidity"], "qty": stake / ref, "cost_usd": stake,
               "entry_fee_usd": costs.fee(stake)}
        res = replay_position(pos, ticks, cfg, costs)
        if res is None:
            continue
        k = stats[sh["kind"]]
        k.kind = sh["kind"]
        k.count += 1
        k.would_pnl += res.pnl_usd
        k.would_win += res.pnl_usd > 0
        if len(k.examples) < 5:
            k.examples.append(f"{sh['symbol'] or sh['token'][:6]} {res.pnl_usd:+.2f} $ ({res.reason})")
    return sorted(stats.values(), key=lambda k: -k.count)


def render_shadows(stats: list[KindStats]) -> str:
    if not stats:
        return "Aucun refus suivi pour l'instant (il faut ~30 min de prix après chaque refus)."
    lines = ["Refus suivis — « et si on avait acheté 5 $ ? » (règles de sortie actuelles, frais compris)"]
    for k in stats:
        lines.append(f"• {k.kind} : {k.count} refus, {k.would_win} auraient gagné, total {k.would_pnl:+.2f} $ → "
                     f"{k.verdict}")
        lines += [f"    {ex}" for ex in k.examples]
    return "\n".join(lines)
