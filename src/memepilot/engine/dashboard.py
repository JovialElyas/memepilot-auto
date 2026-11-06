"""Interface de test locale : rend un CycleReport en page HTML autonome (aucune dépendance, aucun serveur).

La page se rafraîchit toute seule ; elle est régénérée à chaque cycle par scripts/dashboard.py.
Tout texte venant de l'API (symboles…) est échappé : une donnée externe ne peut pas injecter de HTML.
"""

from __future__ import annotations

import html
import time
from typing import Any

from memepilot.config.limits import Limits
from memepilot.engine.cycle import CycleReport
from memepilot.risk.guardian import RiskDecision, budgets
from memepilot.risk.state import RiskState
from memepilot.strategy.hybrid import Decision

CSS = """
:root{--bg:#0f1115;--card:#171a21;--line:#262a33;--txt:#e6e8ec;--mut:#8b93a1;--ok:#3ecf8e;--warn:#f5a524;--bad:#f2555a;--acc:#7aa2ff}
@media (prefers-color-scheme: light){:root{--bg:#f6f7f9;--card:#fff;--line:#e3e6eb;--txt:#151820;--mut:#5d6572}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.45 system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:1200px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:22px 0 8px;color:var(--mut);font-weight:600}
.banner{border:1px solid var(--warn);color:var(--warn);border-radius:8px;padding:8px 12px;margin:10px 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px}.kpi b{display:block;font-size:20px}.kpi span{color:var(--mut);font-size:12px}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px}table{border-collapse:collapse;width:100%;min-width:760px;background:var(--card)}
th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{color:var(--mut);font-weight:600;font-size:12px}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}.mut{color:var(--mut)}
.tag{display:inline-block;padding:1px 7px;border-radius:99px;font-size:12px;font-weight:600}
.PROPOSE{background:color-mix(in srgb,var(--ok) 20%,transparent);color:var(--ok)}.NO_TRADE{background:color-mix(in srgb,var(--mut) 18%,transparent);color:var(--mut)}
.LOW{color:var(--ok)}.MEDIUM{color:var(--warn)}.HIGH{color:var(--bad)}.veto{color:var(--bad)}
.bar{height:6px;background:var(--line);border-radius:3px;min-width:60px}.bar i{display:block;height:6px;border-radius:3px;background:var(--acc)}
details summary{cursor:pointer;color:var(--acc)}ul{margin:4px 0 0 16px;padding:0}
"""


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _bar(score: float, conf: float) -> str:
    return (f'<div title="score {score:.0f} · confiance {conf:.2f}"><span class="mono">{score:.0f}</span>'
            f'<span class="mut mono"> ×{conf:.1f}</span><div class="bar"><i style="width:{max(0, min(100, score)):.0f}%;'
            f'opacity:{0.35 + 0.65 * conf:.2f}"></i></div></div>')


def _risk_panel(state: RiskState | None, limits: Limits | None) -> str:
    if state is None or limits is None:
        return ""
    b_day, b_cum = budgets(state, limits)
    flag = ("<span class=veto>LOCK — " + _e(state.lock_reason) + "</span>" if state.locked
            else "<span class=veto>HALT (arrêt d'urgence)</span>" if state.halted else "<span class=LOW>actif</span>")
    return f"""<h2>Risk Guardian (portefeuille papier)</h2><div class=kpis>
<div class=kpi><b>{flag}</b><span>état</span></div>
<div class=kpi><b>{state.realized_equity_usd:.2f} $</b><span>capital réalisé (départ {state.initial_capital_usd} $)</span></div>
<div class=kpi><b>{state.daily_loss_usd:.2f} / {limits.daily_loss_limit_usd} $</b><span>perte du jour</span></div>
<div class=kpi><b>{state.cumulative_loss_usd:.2f} / {limits.maximum_cumulative_loss_usd} $</b><span>perte cumulée</span></div>
<div class=kpi><b>{state.open_exposure_usd:.2f} $</b><span>exposition au pire cas</span></div>
<div class=kpi><b>{max(b_day, 0):.2f} $</b><span>budget de risque restant (jour)</span></div>
<div class=kpi><b>{state.slots_used} / {limits.max_concurrent_positions}</b><span>positions + ordres</span></div>
<div class=kpi><b>{state.trades_today} / {limits.max_trades_per_day}</b><span>trades aujourd'hui</span></div>
</div>"""


def _paper_panel(paper: dict[str, Any] | None) -> str:
    if not paper:
        return ""
    pos = "".join(
        f"<tr><td><b>{_e(p['symbol'] or '?')}</b></td><td class=mono>{_e(p['cost'])} $</td>"
        f"<td class=mono>{_e(p['entry'])}</td><td class=mono>{_e(p['age_min'])} min</td>"
        f"<td class=mono>{_e(p['strategy'])}</td></tr>" for p in paper.get("positions", [])
    ) or "<tr><td colspan=5 class=mut>Aucune position ouverte</td></tr>"
    trades = "".join(
        f"<tr><td class=mono>{_e(t['when'])}</td><td><b>{_e(t['symbol'] or '?')}</b></td>"
        f"<td class='mono {'LOW' if t['pnl'] >= 0 else 'HIGH'}'>{t['pnl']:+.2f} $</td><td>{_e(t['reason'])}</td>"
        f"<td class=mono>{_e(t['strategy'])}</td></tr>" for t in paper.get("trades", [])
    ) or "<tr><td colspan=5 class=mut>Aucun trade fermé</td></tr>"
    return f"""<h2>Trading papier</h2><div class=kpis>
<div class=kpi><b>{paper['realized_pnl']:+.2f} $</b><span>P&amp;L réalisé total (frais inclus)</span></div>
<div class=kpi><b>{paper['unrealized']:+.2f} $</b><span>P&amp;L latent</span></div>
<div class=kpi><b>{paper['closed']}</b><span>trades fermés (verdict ≥ 50)</span></div>
<div class=kpi><b>{paper['win_rate']:.0%}</b><span>win rate</span></div>
</div>
<h2>Positions ouvertes</h2><div class=wrap><table><tr><th>Token</th><th>Coût</th><th>Prix d'entrée</th><th>Âge</th><th>Stratégie</th></tr>{pos}</table></div>
<h2>Derniers trades</h2><div class=wrap><table><tr><th>Fermé</th><th>Token</th><th>P&amp;L</th><th>Raison</th><th>Stratégie</th></tr>{trades}</table></div>"""


def render(rep: CycleReport, *, key_label: str, refresh_s: int, history: list[dict[str, Any]],
           risk_state: RiskState | None = None, limits: Limits | None = None,
           verdicts: dict[str, RiskDecision] | None = None, paper: dict[str, Any] | None = None) -> str:
    verdicts = verdicts or {}
    d = rep.to_dict()
    s = d["scan"]
    rows = []
    for dec in sorted(rep.decisions, key=lambda x: -x.score)[:60]:
        sig = {x.module: x for x in dec.signals}
        cells = "".join(
            f"<td>{_bar(sig[m].score, sig[m].confidence) if m in sig and not sig[m].abstains else '<span class=mut>—</span>'}</td>"
            for m in ("momentum", "smart_money", "early_token")
        )
        details = "".join(
            f"<li><b>{_e(x.module)}</b> : {_e(' · '.join(x.reasons))}"
            + (f" <span class=veto>VETO : {_e(' ; '.join(x.vetoes))}</span>" if x.vetoes else "") + "</li>"
            for x in dec.signals
        )
        rows.append(
            f"<tr><td><b>{_e(dec.symbol or '?')}</b><br><a class='mono' target=_blank rel=noopener "
            f"href='https://gmgn.ai/sol/token/{_e(dec.address)}'>{_e(dec.address[:6])}…{_e(dec.address[-4:])}</a></td>"
            f"<td><span class='tag {dec.decision}'>{_e(dec.decision)}</span></td>"
            f"<td class=mono><b>{dec.score:.0f}</b></td>{cells}"
            f"<td class='{dec.risk_class}'>{_e(dec.risk_class)}</td>"
            f"<td>{_verdict(verdicts.get(dec.address))}</td>"
            f"<td><details><summary>{_e(dec.reasons[0][:70])}</summary><ul>{details}</ul></details></td></tr>"
        )
    reasons = "".join(f"<tr><td>{_e(k)}</td><td class=mono>{v}</td></tr>" for k, v in s["reject_reasons"].items())
    no_trade = "".join(f"<tr><td>{_e(k)}</td><td class=mono>{v}</td></tr>" for k, v in d["no_trade_reasons"].items())
    hist = "".join(
        f"<tr><td class=mono>{_e(time.strftime('%H:%M:%S', time.localtime(h['ts'])))}</td><td class=mono>{h['survivors']}</td>"
        f"<td class=mono>{h['proposals']}</td><td class=mono>{h['best']:.0f}</td><td class=mono>{h['calls']}</td>"
        f"<td>{'⚠ 429' if h['rate_limited'] else 'ok'}</td></tr>"
        for h in reversed(history[-20:])
    )
    errors = "".join(f"<li>{_e(x)}</li>" for x in [*s["source_errors"], *d["errors"]])
    best = max((x.score for x in rep.decisions), default=0.0)
    return f"""<!doctype html><html lang=fr><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta http-equiv=refresh content="{refresh_s}">
<title>MemePilot — test</title><style>{CSS}</style></head><body><main>
<h1>MemePilot Auto V2 — environnement de test</h1>
<div class=mut>Dernier cycle : {_e(time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(rep.finished_at)))} ·
clé : {_e(key_label)} · rafraîchissement {refresh_s} s</div>
<div class=banner>Mode PAPIER — aucune transaction possible. Une « PROPOSE » est une hypothèse journalisée, pas un ordre.</div>
<div class=kpis>
<div class=kpi><b>{s['rows_seen']}</b><span>tokens vus</span></div>
<div class=kpi><b>{len(rep.scan.survivors)}</b><span>passent la sécurité</span></div>
<div class=kpi><b>{len(rep.proposals)}</b><span>propositions</span></div>
<div class=kpi><b>{best:.0f}</b><span>meilleur score (seuil 70)</span></div>
<div class=kpi><b>{_e(s['sol_usd'] and round(s['sol_usd'], 2))}</b><span>SOL / USD</span></div>
<div class=kpi><b>{s['calls'] + 2 + d['klines_fetched']}</b><span>appels GMGN</span></div>
<div class=kpi><b>{d['feed_trades']}</b><span>trades smart money lus</span></div>
</div>
{_risk_panel(risk_state, limits)}
{_paper_panel(paper)}
{f'<h2>Erreurs</h2><ul>{errors}</ul>' if errors else ''}
<h2>Décisions (60 meilleurs scores)</h2>
<div class=wrap><table><tr><th>Token</th><th>Décision</th><th>Score</th><th>Momentum</th><th>Smart money</th>
<th>Early token</th><th>Risque</th><th>Guardian</th><th>Raison</th></tr>{''.join(rows) or '<tr><td colspan=9 class=mut>Aucun survivant</td></tr>'}</table></div>
<h2>Pourquoi pas de trade</h2><div class=wrap><table><tr><th>Raison NO_TRADE</th><th>Nb</th></tr>{no_trade}</table></div>
<h2>Rejets de sécurité (préfiltre)</h2><div class=wrap><table><tr><th>Motif</th><th>Nb</th></tr>{reasons}</table></div>
<h2>Historique des cycles</h2><div class=wrap><table><tr><th>Heure</th><th>Survivants</th><th>Propositions</th>
<th>Meilleur</th><th>Appels</th><th>Débit</th></tr>{hist}</table></div>
<p class=mut>Données : GMGN OpenAPI via gmgn-cli (lecture seule). Seuils : docs/STRATEGY.md.</p>
</main></body></html>"""


def _verdict(v: RiskDecision | None) -> str:
    if v is None:
        return "<span class=mut>—</span>"
    if v.approved:
        return f"<span class=LOW>✔ {v.size_usd} $</span>"
    return f"<span class=veto title='{_e(' ; '.join(v.reasons))}'>✘ {_e(v.reasons[0][:40])}</span>"


def history_entry(rep: CycleReport, calls: int) -> dict[str, Any]:
    return {
        "ts": rep.finished_at, "survivors": len(rep.scan.survivors),
        "proposals": sum(1 for d in rep.decisions if d.decision is Decision.PROPOSE),
        "best": max((d.score for d in rep.decisions), default=0.0), "calls": calls,
        "rate_limited": rep.scan.rate_limited,
    }
