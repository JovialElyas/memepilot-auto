# GMGN_SKILLS_INVENTORY — Skills GMGN disponibles et utilité pour MemePilot

Source : `GMGNAI/gmgn-skills`, commit `6a04e1f` (2026-10-29), licence **MIT**, gmgn-cli **1.7.1**.

## 1. Deux usages distincts d'un skill

1. **Au développement** (Claude Code) : un skill est un mode d'emploi pour un agent. Il aide à appeler la bonne commande et à lire les bons champs. C'est son usage prévu.
2. **À l'exécution** (bot MemePilot) : le bot n'utilise **pas** les skills. Il appelle `gmgn-cli` via son client Python typé (`memepilot.gmgn`). Les skills servent de **spécification** (champs, seuils, pièges) et, pour 3 d'entre eux, de **code de référence** sous MIT.

## 2. État d'installation

| Où | État |
|---|---|
| Démo `aitrader` | N'utilise **aucun** skill (gmgn-cli direct) |
| Projet `memepilot-auto` | Aucun skill installé dans le dépôt (voulu) |
| Ta machine (Claude Code) | **Inconnu** — je n'ai pas accès à ton dossier de config Claude Code |

**Installation recommandée** (une seule commande, installe les 19 skills et le CLI) :
```bash
npx skills add GMGNAI/gmgn-skills
gmgn-cli --version      # doit afficher 1.7.1 (version épinglée du projet)
```
Il n'y a rien d'autre à installer : le dépôt officiel ne propose pas de skills hors de ce paquet.

## 3. Inventaire

Légende de pertinence : **H** = haute, **M** = moyenne, **B** = basse, **⛔** = interdit dans le bot.

| Skill | Type | Commandes CLI | Pertinence | Usage MemePilot |
|---|---|---|---|---|
| `gmgn-market` | Lecture | `market kline/trending/trenches/signal/hot-searches/search` | **H** | Market Scanner, module momentum (kline), module early token (trenches) |
| `gmgn-token` | Lecture | `token info/security/pool/holders/traders` | **H** | Token Intelligence Agent, filtres durs |
| `gmgn-track` | Lecture | `track smartmoney/kol/follow-wallet/follow-tokens` | **H** | Smart Money Analyst (clusters d'achat, **sorties**) |
| `gmgn-portfolio` | Lecture | `portfolio info/holdings/activity/stats/profits/token-balance/created-tokens` | **H** | Portfolio Manager, réconciliation, historique du créateur |
| `gmgn-swap` | **Exécution** | `swap`, `order quote/get`, `order strategy create/list/cancel`, `gas-price` | **H (Phase 6)** | **Execution Engine uniquement.** Ordres conditionnels TP/SL/trailing, suivi des ordres |
| `gmgn-token-buy` | Mixte (prépare l'ordre) | `token *`, `market kline`, `gas-price` | **H** | `references/thresholds.md` et `pitfalls.md` : seuils de volume, profondeur de pool (≥ 30 k$), impact de prix ≤ 3 %, direction 5 min → à intégrer à STRATEGY |
| `gmgn-contract-dd` | Lecture | `token *` | **H** | Score 0–100 où **un champ absent est signalé et non ignoré** : même philosophie que nous |
| `gmgn-dev-score` | Lecture + `dev_score.py` (955 lignes, MIT) | `portfolio created-tokens/activity`, `token *` | **M-H** | Historique des créateurs (module early token). Trop coûteux en temps réel (25 pages) → mode `brief` ou calcul hors ligne avec cache |
| `gmgn-wallet-score` | Lecture | `portfolio stats/activity/created-tokens` | **M-H** | Sélection hors ligne des wallets smart money à suivre (« bon trader » ≠ « copiable ») |
| `gmgn-wallet-analysis` | Lecture + `analyze.py` (3 247 lignes, MIT) | `portfolio *` | **M** | 4 critères éliminatoires par wallet : idem, hors ligne |
| `gmgn-holder-analysis` | Lecture + `analyze.py` (935 lignes, MIT) | `token holders/traders` | **M** | Concentration, bundlers, snipers, insiders pour le module early token |
| `gmgn-kline-pattern` | Lecture | `market kline` | **M** | Structure de bougies du module momentum (6 mesures, score 0–100) |
| `gmgn-heat-rank` | Lecture | `market trending` (multichaîne) | **B** | Nous restons sur SOL |
| `gmgn-callout` / `gmgn-callout-score` | Lecture | `callout record/global` | **B** | Signal social optionnel, plus tard (Phase 8) |
| `gmgn-narrative` | Lecture + recherche web | — | **B** | Narratif ; trop lent et non déterministe pour la décision |
| `gmgn-wallet-style` / `gmgn-wallet-review` | Lecture | `portfolio *` | **B** | Étiquettes descriptives pour les rapports |
| `gmgn-cooking` | **Exécution (création de token)** | `cooking create/stats` | **⛔** | Création et lancement de tokens : hors périmètre, interdit |

Notes :
- `multi-swap` (dans `gmgn-swap`) est **interdit** : V2 est mono-wallet.
- Le README officiel présente parfois « cooking » comme un achat + conditions, mais `cli-usage.md` 1.7.1 le décrit comme la **création de token**. On l'exclut dans tous les cas.

## 4. Comment les skills alimentent le projet

| Phase | Skills utiles |
|---|---|
| 2 — Lecture de données | `gmgn-market`, `gmgn-token`, `gmgn-portfolio`, `gmgn-track` (contrat de champs) |
| 3 — Agents analytiques | `gmgn-token-buy` (seuils), `gmgn-contract-dd`, `gmgn-kline-pattern`, `gmgn-holder-analysis`, `gmgn-dev-score` |
| 3 — Liste de wallets smart money | `gmgn-wallet-score`, `gmgn-wallet-analysis` (traitement hebdomadaire hors ligne) |
| 6 — Exécution | `gmgn-swap` |

## 5. Règles

- Toute réutilisation de code MIT (`dev_score.py`, `analyze.py`) garde l'en-tête de licence et l'attribution GMGN.
- Aucun skill d'exécution n'est appelé par un agent LLM au runtime.
- Plafonds de quota : plan Free 5/5 (débit/capacité). Les skills `dev-score` et `wallet-analysis` font plusieurs dizaines d'appels par adresse → **jamais dans la boucle temps réel**.
