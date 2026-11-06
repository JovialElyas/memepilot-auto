# ROADMAP — MemePilot Auto V2

Aucune phase n'est sautée. Chaque phase se termine par une revue et ton approbation explicite.

| Phase | Contenu | Livrables | Critères de sortie | Fonds réels |
|---|---|---|---|---|
| **0** | Audit documentaire et architecture | 7 documents | Approbation du propriétaire | Non |
| **1** ⛔ code livré, vérif. wallet bloquée (B-01) | Structure du projet + recherche d'intégration | Squelette Python, pyproject, `.env.example`, CI (ruff, mypy, pytest, import-linter, gitleaks), `limits.yaml` validé par Pydantic ; **test de compatibilité wallet (SECURITY §4, étapes 1–4)** ; choix option A/B | CI verte ; cas A/B/C documenté ; si cas C → STOP | Non |
| **2** 🟡 code livré, validation 30 min bloquée (B-01) | Intégration GMGN lecture seule | `read_client`, modèles Pydantic des réponses, cache + TTL, gestion rate limit, enregistrement de fixtures | Tests d'intégration sur la clé lecture seule ; aucune donnée non validée ne passe | Non |
| **3** ✅ code livré (papier, clé de démo) | Agents analytiques + stratégie hybride | 3 modules, Token Intelligence, Hybrid Strategy, scoring documenté | Tests unitaires sur fixtures (cas positif, négatif, données manquantes, NO_TRADE) | Non |
| **4** ✅ code livré (papier) | Risk Guardian + simulation de portefeuille | `guardian.py` pur, `PortfolioState`, LOCK persistant, hash des limites | 100 % des règles couvertes par tests ; tests de propriétés (Hypothesis) : aucune séquence ne dépasse les limites | Non |
| **5** 🟡 code livré, profil exploration (D-021), période de 2 semaines à lancer — VPS : `DEPLOYMENT_PLAN.md` | Paper trading + ledger | Boucle AsyncIO complète en mode `paper`, modèle de coûts conservateur, ledger, rapports Telegram | ≥ 2 semaines continues sans crash ; ≥ 50 décisions ; rapport hebdo calculé du ledger ; revue des résultats | Non |
| **6** | Exécution en environnement de test sûr | `trade_client`, Execution Engine, machine à états, réconciliation, HALT ; swap de test minimal | Idempotence prouvée (tests de crash/redémarrage) ; latences mesurées ; 1 aller-retour réel confirmé et réconcilié | Montant symbolique |
| **7** | Audit sécurité + déploiement live contrôlé | Durcissement VPS, revue des secrets, revue de code par un agent indépendant, checklist de mise en production | Checklist signée ; démarrage live avec limites initiales ; LOCK testé | Oui (≤ 50 USD, perte max 20 USD) |
| **8** | Surveillance, rapports, optimisation | Rapports hebdo, ajustement des seuils **hors live** puis redéploiement | Toute modification de stratégie repasse par paper trading | Oui |

## Points d'arrêt obligatoires

- Fin de Phase 1 : si la compatibilité wallet n'est pas établie → arrêt et rapport.
- Fin de Phase 5 : si l'espérance après coûts est négative → pas de passage au live ; retour Phase 3.
- En live : `LOCKED` → arrêt complet, analyse post-mortem avant tout redémarrage.

## Prochaines étapes (mises à jour le 2026-11-01, D-023)

| # | Étape | Qui | Critère de fin | Durée estimée |
|---|---|---|---|---|
| 1 | Faire tourner le papier en continu sur le PC (`.\scripts\run_forever.ps1`) | toi | le bot tourne, Telegram reçu | aujourd'hui |
| 2 | Collecte : profil exploration, GMGN + relais gratuit (D-024) — compteur remis à zéro le 2026-11-01 | le bot | ≥ 50 trades fermés | 1–3 semaines |
| 3 | Revue hebdomadaire (rapport Telegram du lundi + interface) | toi + moi | aucun incident ; sorties cohérentes | chaque semaine |
| 4 | Calibration : analyser les 50 trades (entrées gagnantes, sorties trop tôt/trop tard, ex. gains rendus avant le time-stop) → profil v2 figé | moi | profil v2 documenté, testé | 1 session |
| 5 | Validation hors échantillon : ≥ 50 nouveaux trades avec v2 | le bot | espérance > 0 après frais, facteur de profit > 1,2 | 1–3 semaines |
| 6 | Décision d'accès réel : mode signaux (0 $/mois) vs clé GMGN (100 $ de solde) + Plus (29 $/mois) | toi | décision écrite (Q-009) | — |
| 7 | Seulement alors : VPS RackNerd + activation de la CI/CD (`docs/CI_CD.md`), puis Phase 6 | toi + moi | — | — |

Règle : aucune modification de stratégie pendant les étapes 2 et 5 (sinon les mesures ne veulent plus rien dire). Les corrections de bugs et d'interface restent permises.

### Pistes à vérifier à la revue (notées le 2026-11-01, RIEN d'implémenté — décision du propriétaire : on observe d'abord)

| Piste | Observation de départ | Comment trancher |
|---|---|---|
| ✅ D-027 — Pause de 2 h après une perte sur un token | niketyson acheté plusieurs fois | mesurer les refus « pause » et ce que ces tokens ont fait ensuite |
| Seuils du trailing (+15 % / −10 %) | choisis par principe (D-026) | `python scripts\replay_exits.py` après ~30 trades |
| Stop-loss exécuté plus bas que −25 % | BNB vendu à −40 % (chute entre deux contrôles) | distribution des pertes réelles sous le stop dans le ledger |
| Gels GMGN avec la clé personnelle | « gelé 26 s » dès le démarrage (blocage IP hérité de la clé de démo ?) | fréquence des gels sur 24 h dans l'interface / le journal |
| Part des trades sur données relais vs GMGN | colonne `data_source` | P&L par source dans le ledger |
| ✅ D-027 — Tendances 5 min en complément | skill GMGN 5-Min Trending | P&L des trades dont la source contient `trending:5m` vs les autres |
| ✅ D-027 — Positions multiples seulement sous contrôle | demande du propriétaire | en pratique limité par le budget de risque du jour (D-010) : à rediscuter |

### Plus tard (demande du propriétaire, 2026-11-01) — NE PAS faire pendant l'observation
- **Nettoyage et rangement du code** : `engine/paper.py` et `scripts/paper_trade.py` ont beaucoup grossi (boucle rapide, relais, garde-fous, interface) ; extraire les règles d'achat (pause, contrôle) et la gestion des cycles dans des modules dédiés.
- **Revue de complexité** : architecture GMGN + relais (3 sources), nombre de règles empilées (D-021 à D-028) — garder seulement ce que les données justifient après la revue des 50 trades.
