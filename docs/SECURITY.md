# SECURITY — MemePilot Auto V2

## 1. Inventaire des secrets

| Secret | Rôle | Stockage | Jamais dans |
|---|---|---|---|
| `GMGN_API_KEY` | Authentifie toutes les requêtes GMGN | `~/.config/gmgn/.env` (chmod 600) ou variable d'env du service | Git, logs, rapports, prompts LLM |
| `GMGN_PRIVATE_KEY` | Clé **Ed25519 de signature de requêtes** (pas une clé de wallet) | idem | idem |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Notifications | `.env` du projet | idem |

Rappel GMGN (`.env.example` officiel) : si `GMGN_PRIVATE_KEY` fuit, un attaquant peut **forger des requêtes de trading authentifiées** sur le wallet lié. Elle doit donc être traitée comme une clé de wallet en pratique.

**Jamais demandé, jamais manipulé** : phrase de récupération Phantom, clé privée Phantom, clé privée du wallet de trading GMGN.

## 2. Règles

- `.env`, `*.pem`, `*.db` dans `.gitignore` dès le premier commit ; hook `pre-commit` + `gitleaks`.
- Filtre de logs : masque toute valeur correspondant à une variable secrète et tout bloc `-----BEGIN ... KEY-----`.
- La paire Ed25519 est générée **par toi**, sur ta machine (ou le VPS), jamais transmise dans le chat. Le fichier temporaire `/tmp/gmgn_private.pem` proposé par le README GMGN doit être déplacé puis supprimé.
- Clés séparées : une clé API **lecture seule** pour Phases 2–5 ; une clé avec trading + whitelist IP du VPS pour Phase 6+.
- Rotation : régénérer la paire et la clé API à chaque suspicion de fuite et à chaque changement de machine.

## 3. Modèle de menaces

| Menace | Mitigation |
|---|---|
| Fuite de clé API/signature | Fichiers 600, whitelist IP (trading impossible depuis une autre IP), rotation, montant limité sur le wallet |
| Bug → boucle d'ordres | Idempotence, blocage par token, max trades/jour, Risk Guardian, `HALT` |
| Prompt injection via données token (nom, description, tweets) | Les champs API sont des **données**, jamais des instructions ; aucun LLM dans le chemin d'exécution ; validation stricte des adresses (base58 32–44) |
| Agent qui modifie les limites | Limites gelées au démarrage + hash enregistré dans `risk_state` ; écart de hash en live → refus de démarrer |
| Rug / honeypot | Filtres sécurité (voir `STRATEGY.md`) ; **honeypot non détecté par GMGN sur SOL** → filtres alternatifs obligatoires |
| Données périmées / incohérentes | TTL par type de donnée ; rejet si incohérent |
| Rate limit / ban | Respect `X-RateLimit-Reset`, arrêt total sur `RATE_LIMIT_BANNED_MAX`, pas de retry de swap après 429 |
| Horloge décalée | NTP (signature valide ±5 s, rejeu refusé sous 7 s) |
| Compromission VPS | SSH par clé uniquement, pare-feu, utilisateur dédié non-root, mises à jour auto, pas d'autre service exposé |
| Dépendances malveillantes | Versions épinglées, `pip-audit`, `npm audit` sur gmgn-cli |

## 4. Vérification de compatibilité wallet (porte d'entrée de la Phase 6)

Objectif : prouver que le compte GMGN connecté via Phantom peut exécuter des swaps via l'OpenAPI, **sans extraire aucune clé de wallet**.

**Choix du propriétaire (D-009) : cas B — wallet de trading GMGN, alimenté manuellement.** La procédure sert à confirmer que ce wallet est bien lié à la clé API.

Procédure (à faire en Phase 1, lecture seule, sauf étape 5) :
1. Toi : créer la clé API sur `https://gmgn.ai/ai` avec ta clé publique Ed25519.
2. Exécuter `gmgn-cli portfolio info --raw` → noter les wallets liés à la clé.
3. Comparer avec ton adresse Phantom :
   - **Cas A** — l'adresse Phantom apparaît comme wallet lié et éligible au trading → poursuivre.
   - **Cas B** — un wallet de trading GMGN distinct apparaît → c'est **ce** wallet qui tradera ; il faudra le financer (50 USD max) depuis Phantom manuellement. Documenter le modèle de garde (qui détient la clé).
   - **Cas C** — aucun wallet de trading lié → **STOP**, rapport, aucune tentative de contournement.
4. Vérifier dans l'interface GMGN les permissions de la clé (trading activé ? whitelist IP ?).
5. En Phase 6 seulement : un swap de test minimal (montant symbolique), puis `order get` jusqu'à `confirmed`, puis vente.

À confirmer aussi auprès de GMGN (Telegram `t.me/gmgnagentapi` ou docs) : frais de trading GMGN, possibilité de retirer les fonds du wallet de trading, comportement en cas de suppression de la clé API.

## 5. Kill switches

1. `HALT` (fichier ou commande admin) : bloque les achats immédiatement.
2. `LOCKED` (perte cumulée ≥ 20 USD) : persistant, déverrouillage manuel hors processus.
3. Révocation de la clé API sur gmgn.ai : coupe tout depuis l'extérieur.
4. Note : les ordres conditionnels déjà créés côté GMGN continuent à vivre → la procédure d'arrêt inclut `order strategy list` puis annulation si nécessaire.
