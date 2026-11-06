# REUSE_VS_REBUILD — Que reprendre de la démo GMGN ?

Légende :
- **REPRENDRE L'IDÉE** : on réimplémente la logique dans notre architecture typée et testée (pas de copie de code, la démo n'a pas de licence).
- **ADAPTER** : la logique est bonne, mais il faut changer son comportement.
- **REMPLACER** : incompatible avec nos exigences, à refaire.
- **IGNORER** : hors périmètre.

Références `L###` = `aitrader/app.py` @ `416f6d2`.

## 1. Données et intégration

| # | Composant (source) | Dépendances | Limites | Décision | Adaptation proposée |
|---|---|---|---|---|---|
| R1 | `LiveGMGN._check_code` (L237) | gmgn-cli | Ne lit que `msg/message/error` | **REPRENDRE L'IDÉE** | Déjà couvert par notre `unwrap()` ; ajouter la lecture de `msg`/`error` + test avec fixture d'erreur |
| R2 | `LiveGMGN._cli` (L247) | subprocess, gmgn-cli | Synchrone, `--chain` imposé à tout | **REMPLACER** | Notre `GmgnReadClient` (async, liste blanche, validation, latence mesurée) |
| R3 | `NODE_OPTIONS=--use-system-ca` (L231) | Node ≥ 22 | Utile seulement derrière un proxy TLS | **REPRENDRE L'IDÉE** | Option désactivée par défaut dans `Settings` |
| R4 | `LiveGMGN.wallet_address` (L357) | `portfolio info` | Prend **le premier** wallet SOL trouvé | **ADAPTER** | Ne jamais choisir automatiquement : vérifier que `GMGN_TRADING_WALLET` (configuré) figure dans `portfolio info`, sinon refus de démarrer |
| R5 | `AppState.trending_rows` cache 3 s (L1351) | — | Repli sans limite d'âge | **ADAPTER** | Cache avec horodatage ; données au-delà du TTL = affichage seulement, **jamais** décision |
| R6 | `DEFAULT_TRENDING_CMDS["sol"]` (L137) | — | Seulement Pump.fun | **REPRENDRE L'IDÉE** | Paramètres du scanner dans la config, validés |
| R7 | `MockGMGN` (L416) | — | Données synthétiques | **REPRENDRE L'IDÉE** | Nous utilisons des **fixtures réelles** + faux CLI (déjà en place) ; un générateur synthétique servira aux tests de propriétés |

## 2. Analyse

| # | Composant | Dépendances | Limites | Décision | Adaptation |
|---|---|---|---|---|---|
| R8 | `FeatureExtractor.build_from_row` (L756) + `_f`/`_b` | ligne trending | Absent → 0 / False | **ADAPTER** | Modèle Pydantic `TrendingRow` avec champs `Optional` ; absent = `None` ; normalisation documentée (%→fraction, 0/1→bool). Reprendre la **correspondance des champs** (très utile) |
| R9 | `hard_gates` (L795) | `TokenFeatures` | Seuils lâches, pas de freeze, pas de liquidité min, absent = OK | **ADAPTER** | `SecurityVerdict` avec motifs ; seuils STRATEGY §2 ; règle « absent → REJECT » |
| R10 | `priority_score` (L821) | features | Seulement les % de la ligne trending | **ADAPTER** | Base du **sous-score momentum** ; enrichi avec `market kline` 1m/5m (accélération, volatilité, anti-FOMO) |
| R11 | `LLMJudge.judge` (L1197) | — | Heuristique, nom trompeur | **REPRENDRE L'IDÉE** de la règle `buy_ratio` | Devient une règle déterministe de confirmation dans le module momentum (`buy_ratio` < 0,42 → rejet). Aucun LLM |
| R12 | `dev_score` + `_merge_created` + `_scan_dev_security` + `_dev_reskin` (L674-876) | `token info`, `portfolio created-tokens`, `token security` ×3 | ≈5 appels/dev ; poids heuristiques | **ADAPTER** | Sous-score « créateur » du module early token. Cache persistant (SQLite) par créateur, TTL 1 h, budget d'appels. Comparer avec `gmgn-dev-score` (MIT) et garder le meilleur |
| R13 | `get_dev_profile` cache TTL (L878) | — | Ne met pas les échecs en cache (bien) | **REPRENDRE L'IDÉE** | Cache générique avec TTL et sans cache d'échec |
| R14 | `_fetch_dev_profiles` pool de 8 threads (L891) | ThreadPoolExecutor | Pic de requêtes → limite de débit Free 5/5 | **ADAPTER** | `asyncio.Semaphore` + régulateur de débit global (jetons pondérés par route) |
| R15 | `sanitize` / `INJECTION_PAT` (L624) | — | gmgn-cli nettoie déjà | **REPRENDRE L'IDÉE** | Nettoyer les symboles avant Telegram et les rapports |
| R16 | Évaluation des wallets : `track_record_score`, `copytrade_score`, `copytrade_backtest`, `wallet_tags` (L908-1190) | `portfolio stats/activity` | Non calibré, coûteux | **ADAPTER** | Traitement **hebdomadaire hors ligne** produisant une **liste blanche** de wallets smart money (table `smart_wallets`). Comparer avec `gmgn-wallet-score` (MIT) |

## 3. Positions, risque, exécution

| # | Composant | Limites | Décision | Remplacement |
|---|---|---|---|---|
| R17 | `assess_escape` (L1236) | Pas de liquidité, de vente du dev ni de sortie smart money ; alerte seulement | **ADAPTER** | Conserver les 3 signaux + ajouter : liquidité −40 %, `creator_token_status` → vente, sortie de cluster smart money, données périmées. Déclenche une **sortie automatique** via l'Execution Engine |
| R18 | `monitor_positions` (L1573) | Dépend de l'interface (pas de boucle) | **ADAPTER** | Tâche AsyncIO indépendante ; garder l'idée de réutiliser la ligne trending pour économiser le quota |
| R19 | `RiskManager` (L1270) | Mémoire, unités SOL, pas de perte cumulée, pas de pire cas, pas de remise à zéro quotidienne | **REMPLACER** | `risk/guardian.py` pur + `risk_state` en SQLite (D-010, F-21b, F-24) |
| R20 | `position_size` (L1257) | Non appliquée à l'achat | **REMPLACER** | Taille = `min(5, budgets restants)` (D-010) |
| R21 | `exit_plan` (L1262) | Affichage seulement | **REMPLACER** | `--condition-orders` réellement envoyés + vérification de `strategy_order_id` |
| R22 | `do_buy` (L1628) | B1–B3, B6–B8, B10 | **REMPLACER** | Machine à états de l'Execution Engine (ARCHITECTURE §4) ; `slippage` en % entier ; `--anti-mev` ; idempotence ; `confirmed` seul = rempli ; prix et montants issus de `report` |
| R23 | `do_sell` (L1692) | B4, B5 | **REMPLACER** | Même machine à états ; position fermée seulement après `confirmed` ; P&L depuis les fills |
| R24 | `LIVE_TRADING_DISABLED` + mode en mémoire | Interrupteur dans le code source, `False` par défaut | **REPRENDRE L'IDÉE** (renforcée) | Déjà en place : `Settings` refuse `live` jusqu'à la Phase 7 ; plus tard, live = flag explicite + vérifications au démarrage + hash des limites |
| R25 | `log()` JSONL (L1404) + `positions.json` | Non atomique, pas de fills ni de frais | **REMPLACER** | Ledger SQLite (WAL), append-only, transactions |

## 4. Interface et divers

| # | Composant | Décision | Raison |
|---|---|---|---|
| R26 | FastAPI + `static/index.html` | **IGNORER** (V2) | Pas d'interface web dans le périmètre ; rapports via Telegram. Un tableau de bord lecture seule sera envisageable en Phase 8 |
| R27 | `/api/config` (clés envoyées par le navigateur) | **IGNORER — anti-modèle** | Nos secrets ne transitent jamais par une API ni par Python (D-012) |
| R28 | Multichaîne, `PUBLIC_DEMO`, GitHub Pages | **IGNORER** | Solana uniquement |
| R29 | Seuils `CFG` (L49) | **REPRENDRE L'IDÉE** | Valeurs utiles comme point de départ de la calibration (Phase 5) |

## 5. Bilan

| Décision | Nombre |
|---|---|
| Reprendre l'idée | 9 |
| Adapter | 10 |
| Remplacer | 7 |
| Ignorer | 3 (dont 1 anti-modèle) |

**Ce qui a le plus de valeur** : R8 (correspondance des champs), R9/R10 (filtres et momentum), R12 (score du créateur), R16 (« bon trader » vs « copiable »), R17 (signaux de sortie).
**Ce qui ne doit surtout pas être repris tel quel** : R19–R25 (risque et exécution).
