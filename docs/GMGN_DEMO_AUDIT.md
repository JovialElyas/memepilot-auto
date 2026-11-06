# GMGN_DEMO_AUDIT — Audit technique de « GMGN AI Trader »

> Audit en lecture seule, 2026-10-31. Aucune modification de la démo, aucun identifiant configuré, aucune transaction.

## 0. Périmètre

| Élément | Valeur |
|---|---|
| Dépôt | `GMGNAI/skillmarket-demos`, commit `416f6d2` (2026-08-21) |
| Dossier audité | `aitrader/` |
| Fichiers lus | `app.py` (1 994 lignes), `README(.en).md`, `SPEC(.en).md`, `WALLET_TAB_NOTES.md`, `requirements.txt`, `static/index.html` (2 018 lignes, survol ciblé) |
| Stack | Python 3.10+, FastAPI + Uvicorn, frontend HTML/JS, `gmgn-cli` en sous-processus |
| Version gmgn-cli visée | 1.3.9 / 1.5.2 (nous : **1.7.1**) |
| Tests automatisés | **Aucun** |
| Licence | **Aucun fichier LICENSE** dans `skillmarket-demos` → droits réservés par défaut. On s'inspire des **idées**, on ne copie pas le code. (Le dépôt `gmgn-skills`, lui, est sous MIT.) |

**Positionnement déclaré (SPEC §2)** : « la machine filtre, l'humain clique ». L'auto-exécution a été **volontairement abandonnée**. C'est l'écart fondamental avec MemePilot Auto V2.

Toutes les références `L###` renvoient à `aitrader/app.py`.

---

## 1. Filtrage du marché

Pipeline `screen_once()` (L1414) :

```
market trending (1 appel, ≤100 lignes) → build_from_row (0 appel) → hard_gates
 → priority_score (initial) → dev_score sur les 24 premiers (≈5 appels/dev, cache 600 s)
 → re-tri → 20 premiers → LLMJudge (heuristique) → candidats « ACTION » (non exécutés)
```

- Commande par défaut SOL (L137) : `market trending --platform Pump.fun --platform pump_mayhem --platform pump_mayhem_agent --platform pump_agent --interval 1h --order-by volume --limit 100`.
- Cache trending 3 s par chaîne (`AppState.trending_rows`, L1351).
- ⚠ **Repli sur données périmées** : si l'appel échoue ou renvoie vide, la dernière liste non vide est réutilisée **sans limite d'âge** (`TRENDING_STALE`) puis filtrée comme si elle était fraîche. Incompatible avec SPEC F-04.
- Idée clé confirmée : une ligne `trending` contient déjà presque tous les champs de due diligence → très économe en quota.

## 2. Contrôles de sécurité des tokens

`hard_gates()` (L795), déterministe, ordre fixe :

| Contrôle | Seuil démo | Notre seuil (STRATEGY) |
|---|---|---|
| `is_honeypot` | rejet si vrai | idem (non fiable sur SOL) |
| `renounced_mint` | requis | requis |
| `renounced_freeze_account` | **non vérifié** | requis |
| `buy_tax` / `sell_tax` | ≤ 10 % | idem |
| `rug_ratio` | ≤ **0,60** | ≤ 0,30 |
| `bundler_rate` | ≤ 0,30 | ≤ 0,20 |
| `dev_team_hold_rate` | ≤ 0,10 | ≤ 0,10 |
| `top_10_holder_rate` | ≤ 0,40 | ≤ 0,30 |
| Liquidité minimale | **absente** | ≥ 10 000 USD |
| Wash trading | seulement via `--filter not_wash_trading` de la commande | rejet explicite |

⚠ **Défaut majeur : données manquantes = valeur rassurante.** `_f(None)` → `0.0` et `_b(None)` → `False` (L633-650). Un `rug_ratio`, un `bundler_rate` ou un `is_honeypot` absent **passe** le filtre. Contraire à notre règle « donnée manquante ≠ signal positif ».

Contrôle complémentaire `_security_unsafe()` (L713) : utilisé seulement pour noter l'historique du dev (mint/freeze/honeypot sur SOL).

## 3. Analyse Smart Money

- Réduite à un **comptage** : `sm_confluence = smart_degen_count + renowned_count` (L779), porte 2 : ≥ 1.
- Pas d'appel à `track smartmoney`, `track kol`, `market signal`, ni à `token holders` pour mesurer **qui** est entré, **quand**, **à quel prix**, ni s'ils **sortent**.
- `portfolio stats` marqué « déprécié » (SPEC §6).
- Dans le score, la confluence n'a qu'un poids de 12/106, en logarithme.

→ Pour notre module Smart Money, la démo n'apporte qu'une **porte minimale**.

## 4. Évaluation et notation des wallets

Onglet séparé `/api/wallet` (L1899), entièrement déterministe :

| Fonction | Rôle |
|---|---|
| `_norm_wallet_stats` (L908) | Normalise `portfolio stats` (PnL, win rate, distribution des gains par tranches) |
| `_activity_summary` (L939) | Depuis `portfolio activity` : % d'entrées sous 100 k$ de market cap, % de « flips » < 5 s |
| `track_record_score` (L1057) | Score « est-il vraiment bon » : discipline de stop (poids 0,34), part gagnante, ROI, win rate, taille d'échantillon |
| `copytrade_score` (L1076) | Score « peut-on le copier » : market cap d'entrée, profit par trade, durée de détention vs latence, nombre de trades, type d'avantage |
| `copytrade_backtest` (L1115) | `copie% = wallet% − dérive×latence − 2×slippage − gas%` |
| `wallet_tags` (L977) | 14 étiquettes de style |

**Intérêt fort** pour nous : l'idée de séparer « bon trader » et « copiable » est exactement ce qu'il faut pour choisir les wallets smart money à suivre. **Limite** : poids heuristiques non calibrés (README), 4 pages d'activité par wallet → coûteux, à faire hors ligne. Le skill officiel `gmgn-wallet-score` (MIT) reprend cette logique.

## 5. Utilisation du LLM

- **Aucun LLM réel n'est appelé.** `LLMJudge.judge()` (L1197) est une heuristique de momentum (`buy_ratio`, variations 5 min et 1 h) qui renvoie un verdict `pass/watch/reject` et une « conviction ».
- Principes posés par la SPEC §3, que nous partageons : le LLM n'est jamais dans le flux d'événements, ne fixe ni taille ni seuil, ne touche ni au risque ni aux sorties, et ne reçoit que des données nettoyées (`sanitize()`, L628).

## 6. Intégration GMGN CLI/API

`LiveGMGN` (L224) :
- `subprocess.run(["gmgn-cli", ..., "--chain", chain, "--raw"])`, timeout 25 s, **sans shell**.
- `_check_code()` (L237) : **bonne idée à reprendre** — gmgn-cli peut sortir avec le code 0 tout en renvoyant `code ≠ 0` (limites de débit, quota). Sans ce contrôle, l'erreur passe pour une liste vide.
- Ajoute `NODE_OPTIONS=--use-system-ca` (proxys TLS d'entreprise).
- `_run_cmd()` (L255) exécute une commande trending saisie par l'utilisateur (seul le préfixe `gmgn-cli market trending` est contrôlé côté API).
- `wallet_address()` (L357) : `portfolio info` → `{"wallets":[{"chain","address",…}]}`, puis choisit le premier wallet de la chaîne. **Confirme le modèle du « wallet lié à la clé API ».**
- Un adaptateur `MockGMGN` (L416) génère des données synthétiques.

## 7. Achat et vente

### `do_buy()` (L1628)
1. `RiskManager.gate()`.
2. `token info`, `token security` (instantané d'entrée), `token price`.
3. En LIVE : `swap --from <wallet> --input-token SOL --output-token <token> --amount <lamports> --slippage 0.01`.
4. Interroge `order get` au plus 5 fois à 1 s d'intervalle.
5. Enregistre la position si le statut est `confirmed`/`processed`/`successful`, **et aussi si elle est encore `pending`**.

### `do_sell()` (L1692)
`swap --input-token <token> --output-token SOL --percent 100 --slippage 0.02`, puis supprime la position.

### Défauts constatés

| # | Défaut | Gravité |
|---|---|---|
| B1 | **`--slippage 0.01` / `0.02`** alors que gmgn-cli attend un pourcentage (« 30 = 30 % », `cli-usage.md`). La démo demande donc 0,01 % de slippage → échecs probables sur des memecoins. **Bug probable, à confirmer en test.** | Élevée |
| B2 | `processed` traité comme « rempli ». La doc GMGN : `pending → processed → confirmed`. Seul `confirmed` est final. | Élevée |
| B3 | Une position `pending` est enregistrée et **jamais réconciliée** ensuite. | Élevée |
| B4 | La vente n'interroge pas le statut de l'ordre : la position est supprimée même si la vente échoue sur la chaîne. | Critique |
| B5 | P&L réalisé = variation de prix × taille, **pas** les montants réellement exécutés (`report.input_amount/output_amount`). Frais ignorés. | Élevée |
| B6 | `size_sol` vient du navigateur ; `max_per_trade_sol` n'est **pas** revérifié à l'achat (seule l'exposition totale l'est). | Élevée |
| B7 | Aucune clé d'idempotence ; un double clic ou une relance peut doubler l'ordre. | Élevée |
| B8 | Aucun `--anti-mev`, priority fee ou tip fee explicite. | Moyenne |
| B9 | **Aucun ordre conditionnel (TP/SL) envoyé** : le plan de sortie n'est qu'affiché (`exit_plan()`, aveu dans le README). | Critique pour l'autonomie |
| B10 | Prix d'entrée = `token price` avant l'ordre, pas le prix exécuté. | Moyenne |

## 8. Séparation SHADOW / LIVE

- `ST.mode` en mémoire, `SHADOW` par défaut, retour à SHADOW au redémarrage.
- Passage en LIVE par `/api/mode` (double confirmation côté interface).
- Verrou global `LIVE_TRADING_DISABLED` (L124) — **actuellement `False` dans le dépôt**.
- Il faut aussi `GMGN_PRIVATE_KEY`, sinon gmgn-cli échoue.
- ⚠ `/api/config` accepte la clé API **et la clé de signature** envoyées depuis le navigateur, puis les écrit dans `~/.config/gmgn/.env`. Les secrets transitent par HTTP local et par le JS de la page.
- En SHADOW, une position est enregistrée sans fill ni prix exécuté → le P&L simulé n'intègre ni frais ni slippage.

## 9. Surveillance des positions

`monitor_positions()` (L1573) + `assess_escape()` (L1236) :
- Lancée seulement quand l'interface appelle `/api/run` (onglet ouvert ; mise en pause si l'onglet est masqué). **Pas de boucle de fond.**
- Signaux : honeypot nouvellement détecté (+60), autorité de mint récupérée (+55), top 10 en hausse de plus de 15 points (+22). Seuil d'alerte : 70.
- Économie de quota : réutilise la ligne trending si le token y est encore.
- **Aucune action automatique** : simple alerte « sortir maintenant ».
- Absents : baisse de liquidité (reconnue comme TODO), vente du dev, sortie de la smart money, stop-loss sur le prix, time-stop, trailing.

## 10. Contrôles de risque déterministes

| Contrôle | Présent | Remarque |
|---|---|---|
| Portes de sécurité | ✔ | Données manquantes acceptées (§2) |
| Positions simultanées max | ✔ | **20** (« relâché », à remettre à 2–3) |
| Exposition totale max | ✔ | 1,0 SOL |
| Perte journalière max | ✔ | 0,5 SOL, **en mémoire**, remise à zéro au redémarrage, jamais réinitialisée par jour (pas de notion de date) |
| Kill switch sur pertes consécutives | ✔ | 3 pertes, en mémoire |
| Perte cumulée max | ✘ | — |
| Exposition au pire cas (D-010) | ✘ | — |
| Nombre de trades par jour | ✘ | — |
| Taille de position | ✔ | `position_size()` : fraction fixe, mais non appliquée à `/api/buy` (B6) |
| Persistance du risque | ✘ | Seules les positions sont sauvegardées |
| Fraîcheur des données | ✘ | Repli sans limite d'âge |

## 11. Fonctions incomplètes ou provisoires

| Élément | État |
|---|---|
| `LLMJudge.judge` | Heuristique provisoire |
| `priority_score` | Pondération manuelle (« ML ranking » prévu) |
| `exit_plan` | Affichage seulement, aucun ordre conditionnel |
| Dédoublonnage des événements | Non implémenté |
| Boucle de retour (P&L → réglage des seuils) | Journal en écriture seule |
| Signal de retrait de liquidité | Retiré, à refaire |
| Persistance de `RiskManager` | Non |
| Suivi de `order get` | 5 essais, pas de réconciliation |
| Chaînes EVM | « semblent bugguées » (README) |
| Écriture de `positions.json` | Non atomique, exceptions avalées (`except: pass`) |

## 12. GMGN Skills

La démo **n'utilise aucun skill** : elle appelle `gmgn-cli` directement. Inventaire et recommandations dans `GMGN_SKILLS_INVENTORY.md`.

---

## 13. Comparaison avec les exigences MemePilot Auto V2

| Exigence | Démo | Écart |
|---|---|---|
| Trading entièrement autonome | ✘ Humain dans la boucle par conception | Total |
| Stratégie hybride momentum + smart money + early token | Momentum ✔ partiel ; smart money = comptage ; early token ✘ (pas de `trenches`) | Important |
| Perte cumulée max 20 $ | ✘ | Total |
| Capital initial 50 $ | Unités en SOL, capital fictif de 10 SOL | Total (pas de conversion USD) |
| Position max 5 $ | Non appliquée côté serveur (B6) | Important |
| Max 2 positions simultanées | Paramètre à 20 | Simple réglage, mais logique à refaire |
| Risk Guardian indépendant | `RiskManager` en mémoire, inclus dans l'état de l'app | Important |
| Execution Engine centralisé unique | Achat/vente dans les routes, sans machine à états ni idempotence | Important |
| Gestion automatique des positions | Alertes seulement, pas de TP/SL | Total |
| Rapports Telegram | ✘ | Total |
| Ledger persistant des transactions | JSONL d'événements, pas de fills, pas de frais | Important |

**Conclusion de l'audit** : la démo est une **excellente référence de recherche** (cartographie des champs, idées de filtrage et de notation des devs et wallets, économie de quota). Ce n'est **pas une base d'exécution** : la partie achat/vente/risque contient des défauts incompatibles avec l'autonomie, et le dépôt n'a pas de licence.
