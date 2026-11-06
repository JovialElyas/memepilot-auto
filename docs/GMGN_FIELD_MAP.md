# GMGN_FIELD_MAP — Contrat de champs (gmgn-cli 1.7.1, Solana)

Établi le 2026-10-31 à partir de **réponses réelles** (clé de démo) enregistrées dans `tests/fixtures/gmgn/`.
Code correspondant : `src/memepilot/gmgn/models.py`. Règle : **absent → `None`**, jamais 0 ni False.

## 1. Enveloppes de réponse

| Commande | Forme brute (`--raw`) | Liste utile |
|---|---|---|
| `market trending` | `{"code":0,"data":{"rank":[…]},"message","reason"}` | `data.rank` |
| `market trenches` | `{"new_creation":[…],"near_completion":[…],"completed":[…]}` | 3 listes |
| `market kline` | `{"list":[…]}` | `list` |
| `track smartmoney` / `track kol` | `{"list":[…]}` | `list` |
| `token holders` | `{"list":[…]}` | `list` |
| `token info` / `security` / `pool` | objet direct | — |
| `portfolio info` | `{"wallets":[{"chain","address",…}]}` | `wallets` |
| Erreur métier en code de sortie 0 | `{"code":≠0,"msg"/"message"/"error":…}` | → `GmgnCallFailed` |

## 2. Champs utilisés par MemePilot

| Concept | trending | trenches | token info | token security | Normalisation |
|---|---|---|---|---|---|
| Liquidité (USD) | `liquidity` | `liquidity` | `liquidity`, `pool.liquidity` | — | texte/nombre → float ; `token pool.liquidity` recoupe |
| Prix (USD) | `price` | `price` | `price.price` | — | — |
| Variation 5 min / 1 h | `price_change_percent5m/1h` (**en %**) | — | `price.price_5m/1h` (prix passés) | — | /100 → fraction (`chg_5m`, `chg_1h`) |
| Achats / ventes | `buys`, `sells` (fenêtre de l'intervalle) | `buys_24h`, `sells_24h` | `price.buys_5m`… | — | `buy_ratio` calculé ; None si 0 transaction |
| Risque de rug | `rug_ratio` | `rug_ratio` | **absent** | **absent** | 0–1 |
| Mint renoncé | `renounced_mint` (0/1) | `renounced_mint` | — | `renounced_mint` (bool) | → bool ; `security` fait foi |
| Freeze renoncé | `renounced_freeze_account` | idem | — | idem | idem |
| Honeypot | `is_honeypot` (0) | — | — | `is_honeypot` = **null sur SOL** | jamais lu comme « sûr » |
| Wash trading | `is_wash_trading` | `is_wash_trading` | — | — | bool |
| Top 10 détenteurs | `top_10_holder_rate` | `top_10_holder_rate` | `stat.top_10_holder_rate` (texte) | `top_10_holder_rate` (texte) | **0 exact = non calculé → rejet** |
| Équipe dev | `dev_team_hold_rate` | `dev_team_hold_rate` | `stat.dev_team_hold_rate` | — | — |
| Créateur | `creator` | `creator`, `creator_balance_rate` | `dev.creator_address`, `stat.creator_hold_rate` | — | — |
| Historique créateur | — | `creator_created_count/open_count/inner_count/open_ratio` | `stat.creator_created_count` | — | module early token |
| Bundlers | `bundler_rate` | `bundler_trader_amount_rate` | `stat.top_bundler_trader_percentage` | — | noms différents ! |
| Rat traders / insiders | `rat_trader_amount_rate` | `rat_trader_amount_rate`, `suspected_insider_hold_rate` | `stat.top_rat_trader_percentage` | — | — |
| Snipers | `sniper_count`, `top70_sniper_hold_rate` | `top70_sniper_hold_rate` | `wallet_tags_stat.sniper_wallets` | — | — |
| Smart money | `smart_degen_count` | `smart_degen_count` | `wallet_tags_stat.smart_wallets` | — | — |
| KOL | `renowned_count` | `renowned_count` | `wallet_tags_stat.renowned_wallets` | — | — |
| Âge | `creation_timestamp` (s) | `created_timestamp` (s) | `creation_timestamp` | — | → minutes |
| Courbe de lancement | `launchpad_status` | `progress` (0–1), `status`, catégorie | `launchpad_progress` | — | — |
| Bougies | — | — | — | — | `market kline` : `time` en **ms** → s ; OHLC en texte |

## 3. Pièges constatés sur données réelles

| # | Constat | Traitement |
|---|---|---|
| P1 | **Token inexistant** : `token info` répond en succès avec des champs vides… **et un prix non nul** (0,000131 $) | `TokenInfo.is_unknown` : pas de symbole et 0 décimale ; le prix n'est jamais une preuve d'existence |
| P2 | `top_10_holder_rate = 0` exactement sur des tokens ayant des détenteurs (ex. 17 détenteurs) | Rejet `top10:zero_suspect` |
| P3 | `rug_ratio`, `is_wash_trading` absents de `token security` | Pris dans trending/trenches (D-011) |
| P4 | Mêmes concepts, noms différents selon la commande (bundlers…) | Normalisés dans `Candidate` |
| P5 | `market trenches --limit` **non respecté** (40 demandés, jusqu'à 60 reçus par catégorie) | Le code ne suppose aucune taille maximale |
| P6 | Tokens `near_completion` : liquidité parfois supérieure à la market cap (courbe de lancement) | À traiter en Phase 3 (on-curve ≠ DEX) |
| P7 | La clé de démo est **partagée** : bannissements 429 fréquents, indépendants de notre débit | Gel global + arrêt du cycle (testé) ; utiliser ta clé |
| P8 | Une même adresse peut apparaître dans plusieurs sources avec des valeurs différentes | Fusion qui comble les trous **sans écraser** : une copie « propre » ne blanchit pas un token risqué (testé) |

## 4. Durées de validité (TTL) par défaut

`sol_price` 30 s · `trending`/`trenches` 45 s · `kline` 60 s · `token_info`/`token_pool` 30 s · `token_security` 300 s · `holders` 120 s · `smart_money` 60 s · `creator` 3 600 s (`market/freshness.py`).
