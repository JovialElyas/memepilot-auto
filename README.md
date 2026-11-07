# MemePilot Auto V2

[![ci-cd](https://github.com/JovialElyas/memepilot-auto/actions/workflows/ci.yml/badge.svg)](https://github.com/JovialElyas/memepilot-auto/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)
![Tests](https://img.shields.io/badge/tests-264%20passed-brightgreen)
![mypy](https://img.shields.io/badge/mypy-strict-informational)

Bot de **trading papier** (simulation) de memecoins sur Solana, construit autour d'une idée simple :
**les garde-fous passent avant la stratégie.** Les données viennent de GMGN en lecture seule ; chaque achat
simulé doit passer un Risk Guardian déterministe dont les limites ne peuvent pas être modifiées par le code.

> ⚠️ **Projet personnel et éducatif.** Aucun ordre réel n'est possible dans cette version, aucun argent n'est
> engagé. Ce dépôt n'est pas un conseil financier et ne promet aucune rentabilité : les résultats ci-dessous
> montrent justement qu'aucune stratégie testée n'a été rentable.

## Ce que fait le projet

- **Scan du marché** toutes les 30 s : tendances et nouveaux tokens (GMGN, relais gratuit DexScreener /
  GeckoTerminal / RugCheck si GMGN est saturé), préfiltre de sécurité (liquidité, détenteurs, autorisations).
- **Analyse** : modules de signaux (momentum, smart money, jeunes tokens) combinés par une stratégie hybride notée sur 100.
- **Risk Guardian** : taille de position, budget de risque du jour, perte cumulée maximale de **20 $ codée en dur**
  (blocage définitif, déverrouillage manuel uniquement), arrêt d'urgence.
- **Trading papier réaliste** : frais (1 % + 0,05 $), glissement selon la liquidité, achat confirmé ~5 s après la
  décision, sorties surveillées toutes les 2 s (stop-loss, trailing, time-stop, chute de liquidité).
- **Traçabilité** : chaque décision, ordre et prix est enregistré dans un ledger SQLite ; rapport hebdomadaire,
  bilan des refus (« et si on avait acheté ? ») et laboratoire de tests d'hypothèses.
- **Supervision** : interface web locale en lecture seule, notifications Telegram sortantes, chien de garde
  (healthchecks.io), redémarrage automatique sous Windows.

## Architecture

![Architecture de MemePilot Auto V2](docs/images/architecture.jpg)

| Bloc | Rôle | Modules principaux |
|---|---|---|
| **Market data** | Accès aux données, limiteur de débit GMGN, relais de secours | `gmgn/`, `relay/`, `market/` |
| **Analysis** | Scan des candidats, préfiltre de sécurité, signaux, stratégie hybride | `scanner/`, `signals/`, `strategy/`, `engine/cycle.py` |
| **Paper trading** | Moteur papier, courtier simulé, règles de sortie, ledger SQLite | `engine/paper.py`, `paper/`, `ledger/` |
| **Risk and reporting** | Guardian, état du risque, interface, Telegram, rapport hebdo | `risk/`, `web/`, `reporting/` |

Détails : [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · Décisions d'architecture (ADR) : [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Sécurité dès la conception

- **Lecture seule** : le client GMGN n'accepte qu'une liste blanche de commandes de lecture. Des tests d'architecture
  vérifient qu'aucun code ne lit les secrets, n'utilise `shell=True` ni n'assouplit les limites de risque.
- **Aucun secret dans le code** : `.env` et les clés sont ignorés par Git ; la clé API se saisit de façon masquée ;
  les journaux et messages passent par un masquage des secrets (testé). Scan gitleaks à chaque push.
- **Limites immuables** : la perte cumulée maximale est validée au chargement de la configuration ; dépasser
  la limite déclenche un LOCK que seul un humain peut lever.
- **Surface réseau minimale** : interface sur `127.0.0.1` uniquement, en lecture seule (GET), avec en-têtes de
  sécurité ; Telegram en sortie seulement ; requêtes HTTP limitées à des hôtes HTTPS fixes.

Voir [`docs/SECURITY.md`](docs/SECURITY.md).

## Résultats : ce que les tests ont montré

Rapport complet : [`docs/MemePilot_rapport_strategies.pdf`](docs/MemePilot_rapport_strategies.pdf).

- **2 sessions de trading papier (~50 trades)** : toutes deux perdantes (−11,83 $ et 37 % de gagnants sur
  l'objectif de 30 trades). Le Guardian a toujours tenu les limites.
- **Laboratoire sur 2 049 candidats** suivis sans acheter : **aucune stratégie rentable** sur les deux moitiés
  de la période (117 variantes de scalping, hypothèse H1 « momentum très fort », hypothèse H2 « suivi de tendance 4 h »).
- **Conclusion** : avec ~4 % de frais par aller-retour et des prix quasi aléatoires de 5 min à 4 h, il n'y a pas
  d'avantage exploitable sur ces memecoins. La méthode (hypothèse fixée avant les données, validation sur deux
  moitiés, ≥ 30 cas) a évité de croire à des résultats chanceux.

## Démarrage rapide (sans clé API, sans réseau)

```bash
git clone https://github.com/JovialElyas/memepilot-auto.git
cd memepilot-auto
python -m venv .venv
.venv\Scripts\activate          # Linux / macOS : source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest                # 264 passed
python scripts/paper_trade.py --offline --cycles 3 --interval 1   # démo hors ligne + interface http://127.0.0.1:8765
```

Utilisation complète (clé GMGN, Telegram, exécution continue, laboratoire) : [`docs/GUIDE.md`](docs/GUIDE.md).

## Structure du dépôt

```text
config/limits.yaml      limites de risque (validées au chargement)
src/memepilot/
  gmgn/                 client gmgn-cli en lecture seule, modèles Pydantic, limiteur de débit
  relay/                relais gratuit (DexScreener, GeckoTerminal, RugCheck)
  market/               prix et fraîcheur des données
  scanner/ signals/     candidats, préfiltre de sécurité, modules de signaux
  strategy/             stratégie hybride (note sur 100)
  engine/               cycle d'analyse, moteur papier, mode hors ligne
  paper/                courtier simulé, coûts, sorties, laboratoire, rejeu
  risk/                 Risk Guardian, état et persistance du risque
  ledger/               ledger SQLite (décisions, ordres, exécutions, positions)
  reporting/ web/       Telegram, chien de garde, rapport hebdo, interface locale
scripts/                points d'entrée (paper_trade, risk_admin, weekly_report, ...)
tests/                  264 tests : unitaires, propriétés (Hypothesis), architecture
docs/                   guide, architecture, sécurité, décisions, rapports
```

## Stack et qualité

Python 3.11+ · asyncio · Pydantic v2 · SQLite · gmgn-cli · pytest + Hypothesis · ruff · mypy `--strict` · GitHub Actions
(lint, typage, tests sur 3 versions de Python, essai hors ligne, scan de secrets).

```bash
python -m ruff check .
python -m mypy
python -m pytest
```

## Auteur

**Jovial Elyas Bearivo** — étudiant en informatique appliquée, Université de Moncton.
[GitHub](https://github.com/JovialElyas) · [LinkedIn](https://www.linkedin.com/in/jovial-elyas-bearivo)
