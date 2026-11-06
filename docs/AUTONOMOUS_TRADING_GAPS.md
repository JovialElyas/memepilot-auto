# AUTONOMOUS_TRADING_GAPS — Ce qui manque pour passer de la démo humain-dans-la-boucle à MemePilot autonome

Chaque écart a un ID (G-xx), une gravité (**C** = critique / bloquant pour le live, **H** = haute, **M** = moyenne), la phase qui le traite et l'exigence couverte.

## 1. Orchestration

| ID | Capacité manquante | Grav. | Phase | Exigence |
|---|---|---|---|---|
| G-01 | Boucle de fond AsyncIO indépendante de toute interface (scan, analyse, décision, surveillance) | C | 5 | Autonomie |
| G-02 | Ordonnanceur avec cadences par tâche (scan 10–30 s, surveillance 5–10 s, réconciliation 60 s, rapport hebdo) | H | 5 | Autonomie |
| G-03 | Régulateur de débit global pondéré par route (plan Free 5/5) + arrêt total sur `RATE_LIMIT_BANNED_MAX` | C | 2 | NF-05 |
| G-04 | Politique de fraîcheur : TTL par donnée, refus de décider sur données périmées | C | 2 | F-04 |
| G-05 | Supervision du processus : redémarrage auto (systemd), heartbeat Telegram, watchdog | H | 7 | Autonomie |
| G-06 | Synchronisation d'horloge vérifiée au démarrage (signature valide à ±5 s) | H | 6 | NF-06 |

## 2. Données et modèles

| ID | Capacité manquante | Grav. | Phase | Exigence |
|---|---|---|---|---|
| G-07 | Modèles Pydantic par commande, champs `Optional`, « absent ≠ sûr » | C | 2 | F-03, F-12 |
| G-08 | Contrat de champs documenté : quel champ vient de quelle commande (D-011) | H | 2 | F-02 |
| G-09 | Conversion **USD ↔ SOL** fiable (les limites sont en USD, les ordres en lamports) avec source de prix et contrôle de cohérence | C | 2/4 | Capital 50 $, position 5 $ |
| G-10 | Dédoublonnage des événements (même token vu par plusieurs modules) | M | 3 | Hybride |

## 3. Stratégie hybride

| ID | Capacité manquante | Grav. | Phase | Exigence |
|---|---|---|---|---|
| G-11 | Module **Smart Money** réel : clusters d'achat (`track smartmoney/kol`, `market signal`), qualité des wallets, **détection des sorties**, décroissance du signal avec le temps | H | 3 | Hybride |
| G-12 | Liste blanche hebdomadaire de wallets (score « copiable », R16) | M | 3 | Hybride |
| G-13 | Module **Early token** : `market trenches`, statut de la bonding curve, historique du créateur (R12), snipers/insiders/fresh wallets | H | 3 | Hybride |
| G-14 | Module **Momentum** sur bougies (`market kline`), anti-FOMO | H | 3 | Hybride |
| G-15 | Agrégation pondérée + confirmation croisée (≥ 2 modules) + `NO_TRADE` explicite | H | 3 | STRATEGY §4 |
| G-16 | Modèle de coûts (frais GMGN, priority fee, tip, slippage) intégré au score attendu | H | 5 | Q-003 |

## 4. Risk Guardian

| ID | Capacité manquante | Grav. | Phase | Exigence |
|---|---|---|---|---|
| G-17 | Guardian **pur**, séparé, avec droit de veto, testé par propriétés | C | 4 | Guardian indépendant |
| G-18 | Perte **cumulée** max 20 $ + état `LOCKED` persistant + déverrouillage manuel | C | 4 | 20 $ max |
| G-19 | Budget au **pire cas** positions ouvertes incluses (D-010) | C | 4 | D-010 |
| G-20 | Compteurs journaliers datés (UTC) persistants : pertes, nombre de trades | C | 4 | 5 $/jour, 10 trades/jour |
| G-21 | Positions simultanées = 2 et taille ≤ 5 $ **vérifiées côté serveur à chaque ordre** | C | 4 | 2 positions, 5 $ |
| G-22 | Empreinte des limites enregistrée ; refus de démarrer si elle change en live | H | 4 | Limites immuables |
| G-23 | Cohérence solde wallet ↔ ledger avant tout achat | H | 6 | F-21 |

## 5. Execution Engine

| ID | Capacité manquante | Grav. | Phase | Exigence |
|---|---|---|---|---|
| G-24 | Module unique d'exécution (seul importateur du client de trading — test d'architecture déjà en place) | C | 6 | Moteur unique |
| G-25 | Clé d'idempotence écrite **avant** l'envoi ; blocage par token tant qu'un ordre est non résolu | C | 6 | F-31, F-33 |
| G-26 | Machine à états complète + `SUBMIT_UNKNOWN` + réconciliation | C | 6 | F-32 |
| G-27 | Paramètres corrects : `--slippage` en % entier (bug B1 de la démo), `--anti-mev`, `--priority-fee`/`--tip-fee` (tip obligatoire avec ordres conditionnels SOL) | C | 6 | F-35 |
| G-28 | Envoi des **ordres conditionnels** (SL, TP partiel, trailing) + vérification de `strategy_order_id`, sinon `order strategy create` | C | 6 | Gestion auto |
| G-29 | Fills réels (`report.input_amount/output_amount`), prix exécuté, frais, slippage réalisé | C | 6 | F-36, ledger |
| G-30 | Réconciliation au redémarrage : ledger ↔ `portfolio holdings` ↔ `order strategy list` | C | 6 | F-38 |
| G-31 | Retry borné (max 1, seulement si l'échec est prouvé) ; jamais après un 429 | C | 6 | F-34 |
| G-32 | Mesure des latences (API et confirmation) | M | 6 | F-39 |
| G-33 | Arrêt d'urgence `HALT` + procédure d'annulation des ordres conditionnels | C | 6 | F-37 |

## 6. Gestion automatique des positions

| ID | Capacité manquante | Grav. | Phase | Exigence |
|---|---|---|---|---|
| G-34 | Surveillance continue + signaux de sortie enrichis (R17) **qui déclenchent une vente** | C | 6 | Gestion auto |
| G-35 | Time-stop, sortie sur vente du dev, sortie sur sortie de la smart money | H | 6 | STRATEGY §5 |
| G-36 | Gestion des conflits : ordre conditionnel GMGN déclenché + sortie locale simultanée → pas de double vente | C | 6 | F-33 |

## 7. Ledger, rapports, Telegram

| ID | Capacité manquante | Grav. | Phase | Exigence |
|---|---|---|---|---|
| G-37 | Ledger SQLite (décisions, ordres, fills, positions, risque, événements) | C | 5 | Ledger |
| G-38 | P&L réalisé et latent calculés depuis les fills | C | 5/6 | F-41 |
| G-39 | Rapport hebdomadaire calculé depuis le ledger (drawdown max, win rate, performance par module…) | H | 5 | F-42 |
| G-40 | Bot Telegram en sortie seule : alertes trade/rejet critique/LOCK/erreur + rapport | H | 5 | Telegram |
| G-41 | Le mode paper produit des fills simulés avec le modèle de coûts (pas de position « gratuite » comme en SHADOW dans la démo) | H | 5 | Paper trading |

## 8. Qualité et sécurité

| ID | Capacité manquante | Grav. | Phase |
|---|---|---|---|
| G-42 | Tests unitaires, d'intégration, de propriétés, de crash/redémarrage (la démo n'en a **aucun**) | C | 2–6 |
| G-43 | Secrets jamais transmis par une API ni lus par Python (anti-modèle `/api/config` de la démo) | C | 1 ✔ |
| G-44 | Durcissement VPS, IPv4 statique, clé API dédiée au VPS | H | 7 |

## 9. Synthèse

- **44 écarts** : 25 critiques, 16 hauts, 3 moyens.
- Déjà couverts par la Phase 1 : G-24 (test d'isolation), G-43.
- Le chemin critique vers l'autonomie : **G-03/G-04/G-07/G-09 → G-17 à G-22 → G-24 à G-31 → G-34/G-36 → G-37/G-38**.
