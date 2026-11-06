# STRATEGY — MemePilot Auto V2

> Aucune promesse de rentabilité. Les seuils ci-dessous sont des **valeurs de départ** à calibrer en paper trading (Phase 5). Chaque seuil est configurable et versionné.

## 1. Principes

1. **NO_TRADE est le résultat par défaut.** Un trade exige que des conditions positives soient prouvées.
2. **Donnée manquante = pénalité ou rejet**, jamais un bonus.
3. **Filtres durs avant score** : un token qui échoue un filtre de sécurité n'est pas scoré.
4. Chaque décision enregistre : signaux bruts, sous-scores, score final, classe de risque, raison.

## 2. Filtres durs (Token Intelligence → `SecurityVerdict`)

Champs officiels `token security` / `token info` / `market trending|trenches` (SOL) — la source exacte de chaque champ est fixée par D-011 :

| Contrôle | Règle de départ | Si donnée absente |
|---|---|---|
| `renounced_mint` | doit être vrai | REJECT |
| `renounced_freeze_account` | doit être vrai | REJECT |
| `rug_ratio` | ≤ 0.3 (seuil GMGN « high risk ») | REJECT |
| Liquidité (`pool.liquidity`) | ≥ 10 000 USD (sous ce seuil GMGN signale un fort impact de prix) | REJECT |
| `stat.top_10_holder_rate` | ≤ 0.30 | REJECT |
| `stat.creator_hold_rate` / `dev_team_hold_rate` | ≤ 0.05 / ≤ 0.10 | REJECT |
| `bundler_trader_amount_rate`, `rat_trader_amount_rate` | ≤ 0.20 chacun | pénalité forte |
| Wash trading flag | faux | REJECT |
| `dev.creator_open_count` élevé + historique de rugs | pénalité | neutre |

Honeypot : non couvert par GMGN sur Solana → compensé par les filtres ci-dessus et, si possible, par un `order quote` de vente simulée (signé sur SOL ; faisabilité à vérifier, voir DECISIONS Q-005).

## 3. Modules de signaux

Chaque module retourne un sous-score 0–100 + confiance (0–1) + liste de raisons.

### 3.1 Momentum
Sources : `market kline` (1m/5m), `market trending` (1m/5m/1h).
- Accélération du prix (pente 5m vs 15m), ratio volume 5m / moyenne 1h.
- Volatilité courte (écart-type des rendements 1m) : trop élevée = pénalité.
- Structure de bougies : pas d'achat après une mèche haute de rejet ou un pump vertical > X % en 5 min (anti-FOMO).

### 3.2 Smart Money
Sources : `track smartmoney`, `track kol`, `market signal --signal-type 12`, `token holders` (tags), skills `gmgn-wallet-score`.
- Nombre de smart wallets acheteurs distincts sur une fenêtre courte (cluster).
- Qualité des wallets (win rate, P&L historique, copy-tradeability).
- **Sorties** de smart money = signal négatif fort.
- Latence : si le cluster date de plus de N minutes, le signal décroît (on arrive tard).

### 3.3 Early token / sniping
Sources : `market trenches` (`new_creation`, `near_completion`, `completed`, `--filter-preset safe`), `token info` (`is_on_curve`), `portfolio created-tokens`.
- Âge du token, statut bonding curve, liquidité au moment de la migration.
- Historique du créateur, `sniper_count`, `fresh_wallet_rate`, `suspected_insider_hold_rate`.
- C'est le module le plus risqué → poids initial le plus faible et taille réduite.

## 4. Agrégation (Hybrid Strategy Agent)

```
score = Σ (poids_i × sous_score_i × confiance_i) / Σ (poids_i × confiance_i)
```
Poids de départ : momentum 0.35, smart money 0.45, early 0.20.
- Exiger **au moins 2 modules ≥ 60** (confirmation croisée).
- `score ≥ 70` → proposition ; sinon `NO_TRADE`.
- Classe de risque : LOW / MEDIUM / HIGH selon filtres + volatilité ; HIGH → taille ÷ 2 ou rejet.

## 5. Sorties (valeurs de départ)

Attachées au swap via `--condition-orders` (`sell-ratio-type buy_amount`) :
- Stop-loss fixe `loss_stop` : −25 %.
- Take-profit partiel `profit_stop` : +50 % → vendre 50 %.
- Trailing `profit_stop_trace` sur le reste : `drawdown_rate` 20 %.
- Time-stop local : sortie si pas de +10 % après 30 min.
- Sortie d'urgence locale : dev qui vend, liquidité −40 %, sortie de cluster smart money.

⚠ Sur des memecoins, un stop-loss peut s'exécuter bien en dessous du seuil (gap, rug). Le Risk Guardian doit raisonner en **perte maximale réaliste = 100 % de la position**, pas en distance de stop.

## 6. Coûts à mesurer avant toute conclusion

Avec des positions de 5 USD, les frais (priority fee, tip fee obligatoire pour les ordres conditionnels SOL, frais GMGN, slippage) peuvent représenter plusieurs % par aller-retour. Le paper trading doit appliquer un **modèle de coûts conservateur**, recalibré avec les mesures réelles de la Phase 6.

## 7. Critères d'évaluation (Phase 5)

Espérance par trade après coûts, win rate, ratio gain moyen/perte moyenne, drawdown max, taux de rejets par filtre, performance par module. Un module dont l'espérance est négative après N trades est désactivé.

## 8. Implémentation (Phase 3, 2026-10-31)

| Élément | Code | Écart avec ce document |
|---|---|---|
| Momentum | `signals/momentum.py` | Bougies 1 min seulement pour les 3 meilleurs candidats (budget). Sans bougies : confiance plafonnée à 0,6 |
| Smart money | `signals/smart_money.py` | Flux `track smartmoney` + `track kol` (30 min, demi-vie 10 min, wallets distincts). Flux indisponible → confiance ≤ 0,4 |
| Early token | `signals/early_token.py` | S'abstient au-delà de 24 h. Vetos : > 500 lancements, ou < 5 % de graduation sur ≥ 10 lancements |
| Agrégation | `strategy/hybrid.py` | Score escompté par la couverture des données (D-018). Risque HIGH → NO_TRADE ; MEDIUM → taille ×0,5 |
| Cycle | `engine/cycle.py` | ≈ 20 jetons de débit / cycle ; journal `data/decisions.jsonl` |

Premier constat sur données réelles (2 cycles, clé de démo) : environ 105 survivants de sécurité, **0 proposition**, meilleur score 59–63. Principales causes de NO_TRADE : confirmation croisée insuffisante (souvent faute de flux smart money récent sur le token) et vetos « usine à tokens ».
