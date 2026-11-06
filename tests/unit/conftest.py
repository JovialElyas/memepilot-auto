"""Réglages communs des tests unitaires."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _instant_paper_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-030 : en test, pas d'attente réelle (confirmation d'achat, revérification, délai de vente).

    Les mécanismes restent exercés : délai 0 = la seconde lecture de prix a lieu tout de suite.
    """
    import memepilot.engine.paper as paper

    monkeypatch.setattr(paper, "ENTRY_CONFIRM_S", 0.0)
    monkeypatch.setattr(paper, "CRASH_RECHECK_S", 0.0)
    monkeypatch.setattr(paper, "SELL_LATENCY_S", 0.0)
