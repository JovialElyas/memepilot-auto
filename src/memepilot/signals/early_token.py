"""Module Early token (STRATEGY §3.3, idée R12 de la démo : qualité du créateur).

Ne s'applique qu'aux tokens jeunes (≤ 24 h). Mesure :
- l'historique du créateur (taux de « graduation » de ses lancements, lanceur en série) ;
- la propreté de la distribution (insiders, snipers, bundlers, wallets neufs, bots) ;
- la fenêtre d'âge (ni trop tôt — non vérifiable —, ni trop tard).
Module le plus risqué : poids le plus faible dans l'agrégation.
"""

from __future__ import annotations

from dataclasses import dataclass

from memepilot.scanner.prefilter import Candidate
from memepilot.signals.base import SignalReport, _Builder


@dataclass(frozen=True)
class EarlyConfig:
    max_age_min: float = 24 * 60
    sweet_spot_min: tuple[float, float] = (15.0, 240.0)
    serial_launcher: int = 50  # plus de 50 tokens lancés = usine à tokens
    serial_veto: int = 500
    min_open_ratio: float = 0.05  # moins de 5 % de graduation = veto


def early_token_signal(c: Candidate, cfg: EarlyConfig = EarlyConfig()) -> SignalReport:  # noqa: B008
    b = _Builder("early_token")
    if c.age_min is None:
        b.reasons.append("âge inconnu : module non applicable")
        return b.build(0.0)
    if c.age_min > cfg.max_age_min:
        b.reasons.append(f"token de {c.age_min / 60:.0f} h : hors périmètre early")
        return b.build(0.0)

    lo, hi = cfg.sweet_spot_min
    if c.age_min < lo:
        age_s = c.age_min / lo * 0.5  # trop tôt : données peu fiables
    elif c.age_min <= hi:
        age_s = 1.0
    else:
        age_s = max(0.2, 1 - (c.age_min - hi) / (cfg.max_age_min - hi))
    b.add("fenêtre d'âge", age_s, 1, f"{c.age_min:.0f} min")

    # Créateur
    if c.creator_open_ratio is not None:
        b.add("graduation du créateur", c.creator_open_ratio / 0.5, 2.5, f"{c.creator_open_ratio:.0%}")
    else:
        b.add("graduation du créateur", None, 2.5)
    if c.creator_created_count is not None:
        n = c.creator_created_count
        b.add("lanceur en série", 1 - n / cfg.serial_launcher if n > 1 else 1.0, 1.5, f"{n} tokens")
        if n >= cfg.serial_veto:
            b.veto(f"usine à tokens ({n} lancements)")
        if c.creator_open_ratio is not None and n >= 10 and c.creator_open_ratio < cfg.min_open_ratio:
            b.veto(f"créateur à {c.creator_open_ratio:.0%} de graduation sur {n} lancements")
    else:
        b.add("lanceur en série", None, 1.5)
    if c.creator_token_status and "sell" in c.creator_token_status:
        b.reasons.append("le créateur a vendu")

    # Distribution
    risky = [x for x in (c.insider_rate, c.sniper_hold, c.bundler) if x is not None]
    if risky:
        b.add("distribution propre", 1 - sum(risky) / 0.5, 2.5,
              f"insiders+snipers+bundlers = {sum(risky):.0%}")
    else:
        b.add("distribution propre", None, 2.5)
    b.add("wallets neufs", None if c.fresh_wallet_rate is None else 1 - c.fresh_wallet_rate / 0.4, 1)
    b.add("bots", None if c.bot_degen_rate is None else 1 - c.bot_degen_rate / 0.6, 0.5)

    return b.build(base_confidence=1.0)
