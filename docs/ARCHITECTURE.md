# ARCHITECTURE — MemePilot Auto V2

## 1. Vue d'ensemble

```
             ┌─────────────── GMGN OpenAPI (openapi.gmgn.ai) ───────────────┐
             │   lecture (API key)                 trading (API key + sig)  │
             └──────────┬──────────────────────────────────────▲────────────┘
                        │                                      │
               ┌────────▼────────┐                     ┌───────┴─────────┐
               │  gmgn_client    │  (lecture seule)    │ gmgn_trade_client│ ← importable UNIQUEMENT par execution/
               └────────┬────────┘                     └───────▲─────────┘
                        │                                      │
 ┌──────────────────────▼───────────────────────┐              │
 │ 1. Market Scanner (AsyncIO, déterministe)    │              │
 └──────────────────────┬───────────────────────┘              │
        candidats       │                                      │
 ┌──────────────────────▼───────────────────────┐              │
 │ 2. Smart Money Analyst  3. Token Intelligence │ → SignalReport (Pydantic)
 └──────────────────────┬───────────────────────┘              │
 ┌──────────────────────▼───────────────────────┐              │
 │ 4. Hybrid Strategy Agent → TradeProposal|NO_TRADE            │
 └──────────────────────┬───────────────────────┘              │
 ┌──────────────────────▼───────────────────────┐              │
 │ 5. Risk Guardian (déterministe, VETO absolu) │              │
 └──────────────────────┬───────────────────────┘              │
                APPROVE │                                      │
 ┌──────────────────────▼───────────────────────┐              │
 │ 6. Portfolio Manager (taille, état, P&L)      │              │
 └──────────────────────┬───────────────────────┘              │
 ┌──────────────────────▼───────────────────────┐              │
 │ 7. Execution Engine ─────────────────────────────────────────┘
 └──────────────────────┬───────────────────────┘
 ┌──────────────────────▼───────────────────────┐
 │ Ledger SQLite (append-only)  → 8. Reporting Agent → Telegram │
 └──────────────────────────────────────────────┘
```

## 2. Composants

| # | Composant | Nature | Entrée → Sortie | Peut trader ? |
|---|---|---|---|---|
| 1 | Market Scanner | Déterministe, AsyncIO | `trending`, `trenches`, `signal` → `Candidate[]` | Non |
| 2 | Smart Money Analyst | Déterministe (+ LLM optionnel pour résumé) | `track smartmoney/kol`, `token holders/traders` → `SignalReport` | Non |
| 3 | Token Intelligence Agent | Déterministe | `token info/security/pool`, `portfolio created-tokens` → `TokenProfile`, `SecurityVerdict` | Non |
| 4 | Hybrid Strategy Agent | Déterministe (scoring) ; LLM hors boucle pour revue de stratégie | `SignalReport[]` → `TradeProposal` / `NoTrade` | Non |
| 5 | Risk Guardian | Fonction pure déterministe | `TradeProposal` + `PortfolioState` + `Limits` → `RiskDecision` | Non (veto) |
| 6 | Portfolio Manager | Déterministe | ledger + `portfolio holdings` → `PortfolioState` | Non |
| 7 | Execution Engine | Déterministe, machine à états | `ApprovedOrder` → `ExecutionResult` | **Oui, seul** |
| 8 | Reporting Agent | Déterministe (calculs) + LLM optionnel (commentaire) | ledger → rapport + Telegram | Non |

**Règle d'isolation** : `gmgn_trade_client` n'est importable que depuis `execution/`. Un test d'architecture (import-linter) fait échouer la CI sinon.

## 3. Intégration GMGN : deux options (décision Phase 1)

| Option | Description | + | − |
|---|---|---|---|
| A. Wrapper CLI | Python appelle `gmgn-cli ... --raw` en sous-processus, parse le JSON | Code officiel, aucun endpoint inventé, signature gérée par GMGN | Latence de démarrage Node (~centaines de ms), dépendance Node |
| B. Client HTTP natif | `httpx` async + réimplémentation de la signature (`{sub_path}:{sorted_qs}:{body}:{timestamp}`, Ed25519) d'après `src/client/signer.ts` | Plus rapide, typé | Réimplémentation à maintenir si GMGN change le schéma |

**Recommandation** : A pour Phases 2–5 (sûreté, rapidité de dev), mesurer la latence, puis B seulement si la mesure le justifie, avec tests de conformité contre A.

## 4. Machine à états d'un ordre

```
CREATED → SUBMITTING → SUBMITTED(order_id) → PENDING/PROCESSED → CONFIRMED
                │                                   │
                └→ SUBMIT_UNKNOWN ──(réconciliation)─┴→ FAILED / EXPIRED
```
- `CREATED` est écrit en base **avant** l'appel réseau (idempotence).
- `SUBMIT_UNKNOWN` (timeout, coupure) bloque le token jusqu'à réconciliation via `order get` / `portfolio token-balance`.
- Polling `order get` : intervalle et nombre configurables (le skill officiel suggère 3 × 5 s ; à mesurer).

## 5. Sorties de position

Deux couches, la plus stricte gagne :
1. **Ordres conditionnels côté GMGN** attachés au swap (`--condition-orders` : `profit_stop`, `loss_stop`, `profit_stop_trace`, `loss_stop_trace`). Avantage : fonctionnent même si le bot est arrêté. La création est *best-effort* → vérifier `strategy_order_id` et sinon créer via `order strategy create`.
2. **Surveillance locale** (AsyncIO, déterministe) : time-stop, sortie sur alerte sécurité (dev qui vend, chute de liquidité), arrêt d'urgence.

## 6. Modèle de données (SQLite, esquisse)

- `decisions(id, ts, token, strategy, signals_json, score, risk_class, decision, reasons_json)`
- `orders(id, idempotency_key UNIQUE, decision_id, side, token, amount_in, slippage_cap, state, order_id, tx_hash, submitted_at, confirmed_at, api_latency_ms, confirm_latency_ms, error)`
- `fills(order_id, input_amount, output_amount, price_exec, price_ref, slippage_pct, fees_sol, fees_usd)`
- `positions(id, token, opened_at, closed_at, cost_usd, qty, realized_pnl_usd, status, strategy_order_ids)`
- `risk_state(id=1, cumulative_realized_loss_usd, locked, locked_at, limits_hash)`
- `events(ts, level, kind, payload_json)` — alertes sécurité, erreurs, rate limits.

## 7. Arborescence cible (Phase 1)

```
memepilot-auto/
  pyproject.toml   .env.example   config/limits.yaml
  src/memepilot/
    config/  gmgn/{read_client.py,trade_client.py,models.py}
    scanner/  analysts/{smart_money.py,token_intel.py,momentum.py}
    strategy/  risk/guardian.py  portfolio/  execution/  ledger/  reporting/
    app.py  (boucle principale AsyncIO)
  tests/{unit,integration,architecture}/
  docs/ (ces 7 fichiers)
```

## 8. Rôle des LLM

- **Claude Code** : agent de développement uniquement.
- LLM à l'exécution (optionnel) : revue de stratégie hebdomadaire, explication des décisions, commentaire du rapport. Jamais dans le chemin critique achat/vente, jamais dans le Risk Guardian.
