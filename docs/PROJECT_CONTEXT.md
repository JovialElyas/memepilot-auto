# PROJECT_CONTEXT — MemePilot Auto V2

> Phase 0 — Audit documentaire et architecture. Rédigé le 2026-10-31.
> Statut : **en attente d'approbation avant la Phase 1.**

## 1. Objectif

Construire un système de trading autonome de memecoins sur Solana, basé sur les GMGN Agent Skills / OpenAPI, développé avec Claude Code.

Le but n'est **pas** de promettre un rendement. Le but est :
- une exécution contrôlée et vérifiable ;
- une performance mesurable (ledger, rapports) ;
- la préservation du capital (perte cumulée max **20 USD**, immuable en live).

## 2. Paramètres fixés par le propriétaire

| Paramètre | Valeur |
|---|---|
| Réseau | Solana |
| Plateforme | GMGN (OpenAPI + gmgn-cli) |
| Wallet d'exécution | Wallet de trading intégré à GMGN, alimenté manuellement (D-009) ; liaison à la clé API à confirmer en Phase 1 |
| Budget de risque | Pire cas, positions ouvertes incluses : max 5 USD à risque simultanément, ≥ 30 USD toujours protégés (D-010) |
| Autonomie | Entièrement autonome (après validation des phases) |
| Capital initial | 50 USD |
| Perte cumulée maximale | 20 USD (immuable en live) |
| Position max | 5 USD |
| Positions simultanées max | 2 |
| Perte journalière max | 5 USD |
| Trades max / jour | 10 |
| Levier / rechargement auto / retraits externes | Interdits |
| Stratégie | Hybride : momentum + smart money + early token |

## 3. Stack technique retenue

| Élément | Technologie |
|---|---|
| Langage principal | Python 3.12+ |
| Agent de développement | Claude Code |
| Données et exécution | GMGN Skills / OpenAPI (`https://openapi.gmgn.ai`) |
| Réseau | Solana |
| Stockage initial | SQLite (mode WAL) |
| Validation des données | Pydantic v2 |
| Surveillance | AsyncIO |
| Rapports | Python + Telegram Bot |
| Tests | Pytest (+ pytest-asyncio) |
| Déploiement | VPS Linux (IPv4 statique), après validation |

## 4. Historique

- **MemePilot V1 (oct. 2026)** : assistant supervisé, chaque achat/vente approuvé manuellement, capital plafonné à 10 USD, mode papier par défaut.
- **MemePilot Auto V2 (ce projet)** : passage à l'autonomie complète, capital 50 USD, garde-fous codés de façon déterministe. Ce changement est consigné dans `DECISIONS.md` (D-001).

## 5. Ce que l'audit GMGN a établi (résumé)

Source : dépôt officiel `github.com/GMGNAI/gmgn-skills`, commit `6a04e1f` (2026-10-29), `gmgn-cli` v1.7.1.

1. **Authentification** : clé API (`GMGN_API_KEY`) créée sur `https://gmgn.ai/ai` à partir d'une **clé publique Ed25519** générée localement. Les routes de trading exigent une signature de requête avec la clé privée Ed25519 (`GMGN_PRIVATE_KEY`).
2. **`GMGN_PRIVATE_KEY` n'est PAS une clé de wallet blockchain** : c'est une clé de signature de requêtes API. Aucune phrase de récupération Phantom n'est nécessaire ni demandée.
3. **Les swaps s'exécutent depuis un wallet « lié à la clé API »** (`--from` doit correspondre au wallet lié ; `gmgn-cli portfolio info` liste ces wallets). La signature on-chain est donc faite côté GMGN.
4. **Whitelist IP** requise pour activer le trading sur la clé. **IPv6 non supporté.**
5. Ordres disponibles : market, limit, TP/SL fixes, **trailing TP/SL** (`profit_stop_trace` / `loss_stop_trace` + `drawdown_rate`), cooking (achat + conditions), suivi via `order get`, annulation via `order strategy cancel`.
6. **Attention** : la détection honeypot (`is_honeypot`) est documentée pour BSC/Base, **pas pour Solana**. Sur SOL il faut s'appuyer sur `renounced_mint`, `renounced_freeze_account`, `rug_ratio`, concentration des holders, etc.

## 6. Incertitude principale (choix fait : wallet de trading GMGN, D-009 — vérification en Phase 1)

**On ne sait pas encore si le wallet lié à la clé API d'un compte GMGN connecté via Phantom est l'adresse Phantom elle-même ou un wallet de trading géré par GMGN.** GMGN ne peut pas signer avec une clé Phantom sans détenir cette clé ; l'hypothèse la plus probable est donc un wallet de trading GMGN distinct. À vérifier en Phase 1 (procédure dans `SECURITY.md` §4). Si la compatibilité n'est pas établie → **STOP** et rapport, sans contournement.

## 7. Documents de la Phase 0

| Fichier | Contenu |
|---|---|
| `PROJECT_CONTEXT.md` | Ce document |
| `SPECIFICATION.md` | Exigences fonctionnelles et non fonctionnelles |
| `ARCHITECTURE.md` | Composants, flux, interfaces, modèle de données |
| `SECURITY.md` | Secrets, wallet, menaces, vérification de compatibilité |
| `STRATEGY.md` | Modules de signaux, scoring, règles de sortie |
| `ROADMAP.md` | Phases 0 → 8 avec critères de sortie |
| `DECISIONS.md` | Journal des décisions et questions ouvertes |
