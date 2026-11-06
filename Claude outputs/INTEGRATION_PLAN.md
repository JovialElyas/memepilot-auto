# INTEGRATION_PLAN — De l'audit à l'implémentation

> Synthèse des audits (CLI, skills, démo, Phantom). Rien n'est implémenté sans ton approbation.

## 1. Principes retenus

1. **D-014** : la démo n'est ni forkée ni copiée ; 19 idées réimplémentées (`REUSE_VS_REBUILD.md`).
2. Le bot appelle **gmgn-cli 1.7.1** via notre client Python ; les skills servent de spécification et d'aide au développement.
3. Les données manquantes ou périmées bloquent la décision. Filtres serveur **et** filtres client.
4. Plan **Free** pour les Phases 2 à 5 ; plan **Plus** requis avant la Phase 6 (Q-009).

## 2. Clôture de la Phase 1 (actions de ton côté)

| # | Action | Résultat attendu |
|---|---|---|
| 1 | `python -m pip install -e ".[dev]"` puis `python -m pytest` | 47 passed sous Windows |
| 2 | `python scripts\check_wallet_binding.py --demo` | Connectivité OK |
| 3 | `gmgn-cli config` → lien pré-rempli → création d'une clé **lecture seule** | Clé API |
| 4 | Enregistrement de la clé (ENVIRONMENT_SETUP §4) | `.env` local |
| 5 | `python scripts\check_wallet_binding.py --expect <wallet de trading GMGN>` | CAS B confirmé |
| 6 | Relever le tarif du plan Plus sur `gmgn.ai/ai` | Donnée pour Q-009 |

## 3. Phase 2 — Connecteur de données en lecture seule (proposition)

### 3.1 Livrables

| Module | Contenu | Réf. |
|---|---|---|
| `gmgn/rate_limiter.py` | Seau à jetons pondéré par route (tableau de poids officiel), plan configurable, gel global sur `RATE_LIMIT_BANNED*`, respect de `X-RateLimit-Reset` | G-03 |
| `gmgn/models.py` | Modèles Pydantic : `TrendingRow`, `TrenchesRow`, `TokenInfo`, `TokenSecurity`, `TokenPool`, `Holder`, `SmartMoneyTrade`, `Kline`, `WalletInfo` ; champs `Optional`, normalisation documentée (%→fraction, 0/1→bool, texte→Decimal) | G-07, R8 |
| `gmgn/field_map.md` | Contrat de champs : quel champ vient de quelle commande, unité, type observé | G-08, D-011 |
| `gmgn/read_client.py` | + paramètres de filtres serveur, + `_check_code` étendu (R1), + cache TTL horodaté (R5) | G-04 |
| `market/freshness.py` | `Fresh[T]` = valeur + horodatage + TTL ; une donnée expirée lève une erreur à l'usage | F-04 |
| `market/prices.py` | Prix SOL/USD (source : `token info` sur le mint SOL) + contrôle de cohérence | G-09 |
| `scripts/record_fixtures.py` | Enregistre de nouvelles fixtures réelles (clé de démo ou la tienne) | Tests |
| `scripts/scan_once.py` | Un cycle de scan en **SHADOW** : liste les candidats + motifs de rejet, **aucune décision d'achat** | Démo Phase 2 |

### 3.2 Tests

- Parsing de chaque fixture réelle + variantes (champ absent, mauvais type, enveloppe `{code≠0}`).
- Régulateur de débit : jamais au-dessus de la capacité, gel sur bannissement (horloge simulée).
- Fraîcheur : une donnée expirée ne peut pas être lue pour décider.
- Marqueur `live_api` : tests contre l'API réelle, désactivés par défaut.

### 3.3 Critères de sortie

- `scan_once.py` tourne 30 min sur ta machine sans erreur 429.
- 100 % des fixtures sont parsées ; les champs absents arrivent en `None`, jamais en 0 ou False.
- Couverture ≥ 90 % sur `gmgn/`.

## 4. Phases suivantes (rappel avec les écarts G-xx)

| Phase | Contenu principal | Écarts traités |
|---|---|---|
| 3 | Modules momentum / smart money / early token, Token Intelligence, agrégation + `NO_TRADE` ; liste blanche de wallets hebdomadaire | G-10 à G-15, R9–R16 |
| 4 | Risk Guardian pur + `risk_state` SQLite + budget au pire cas | G-17 à G-22 |
| 5 | Boucle AsyncIO, paper trading avec modèle de coûts, ledger, Telegram, rapport hebdo ; **décision économique** (espérance après coûts + coût du plan GMGN) | G-01, G-02, G-16, G-37 à G-41 |
| 6 | Plan Plus, clé dédiée VPS, Execution Engine, ordres conditionnels, réconciliation, HALT, swap de test | G-23 à G-36 |
| 7 | Audit sécurité, VPS, live contrôlé | G-05, G-44 |

## 5. Décisions à valider

| ID | Question | Recommandation |
|---|---|---|
| A-1 | Lancer la Phase 2 telle que décrite au §3 ? | Oui, dès que la Phase 1 est close (§2) |
| A-2 | Méthode de mise en place des clés | Lien pré-rempli via `gmgn-cli config` + enregistrement via notre `--set-api-key` adapté pour lire `keypair.pem` (petite modification) |
| A-3 | Plan GMGN | Rester sur Free jusqu'à la fin de la Phase 5 ; décider de Plus selon le résultat du paper trading et le tarif |
| A-4 | Installer les skills GMGN pour Claude Code | Oui : `npx skills add GMGNAI/gmgn-skills` (aucune clé requise) |

## 6. Recommandation technique

1. **L'architecture est validée par les audits** : le modèle « wallet lié à la clé, signé par GMGN » permet l'autonomie sans aucune clé de wallet, et la démo le prouve en pratique sur Solana.
2. **Le principal risque n'est plus technique mais économique** : le trading exige un plan payant, et sur 50 $ de capital ce coût fixe peut annuler l'espérance. On le mesurera en Phase 5 avant de dépenser quoi que ce soit.
3. **La meilleure prochaine étape** est de clore la Phase 1 sur ta machine (§2), puis de lancer la Phase 2. C'est faisable entièrement sur le plan Free, sans risque financier.
