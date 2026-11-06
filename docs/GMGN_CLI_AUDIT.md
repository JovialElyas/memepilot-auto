# GMGN_CLI_AUDIT — gmgn-cli 1.7.1 : capacités, authentification, restrictions

Méthode : `--help` de **toutes** les commandes (hors ligne, même version que sur ton PC), lecture du code source (`src/`) et de `docs/cli-usage.md`, appels réels en lecture avec la clé de démo publique. Aucune transaction.

## 1. Arbre des commandes et classification

Classes : **L** = lecture, **E** = exécution (déplace des fonds ou modifie un ordre), **C** = configuration locale.
Auth : **K** = clé API seule ; **S** = clé API + signature Ed25519.
Poids = coût dans le seau de débit (« leaky bucket ») ; capacité **Free 5 / Plus 20 / Pro 50**.

| Commande | Classe | Auth | Poids | Free ? | Usage MemePilot |
|---|---|---|---|---|---|
| `token info` | L | K | 1 | ✔ | Token Intelligence, prix |
| `token security` | L | K | 1 | ✔ | Filtres durs |
| `token pool` | L | K | 1 | ✔ | Liquidité, profondeur |
| `token holders` | L | K | 5 | ✔ (rafale 1) | Concentration, tags |
| `token traders` | L | K | 5 | ✔ (rafale 1) | Smart money sur un token |
| `market trending` | L | K | 3 | ✔ | Scanner (+ ~40 filtres serveur) |
| `market trenches` | L | K | 2 | ✔ | Early token (+ ~60 filtres serveur, préréglages `safe`/`smart-money`/`strict`) |
| `market signal` | L | K | 1 | ✔ | Smart money (type 12 = achats smart money) |
| `market kline` | L | K | 2 | ✔ | Momentum (30s, 1m, 5m… ; 1s = Pro) |
| `market hot-searches` | L | K | 3 | ✔ | Facultatif |
| `market search` | L | K | 1 | ✔ | Facultatif |
| `portfolio info` | L | K | 2 | ✔ | **Wallets liés à la clé** |
| `portfolio holdings` | L | **S** | 2 | ✔ | Réconciliation |
| `portfolio activity` / `stats` / `profits` | L | K | 3 | ✔ | Analyse des wallets (hors ligne) |
| `portfolio token-balance` | L | K | 2 | ✔ | Contrôle de solde avant ordre |
| `portfolio created-tokens` | L | K | 2 | ✔ | Historique du créateur |
| `track smartmoney` / `track kol` | L | K | 1 | ✔ | Smart Money Analyst |
| `track follow-tokens` / `follow-token-groups` | L | K | 3 / 1 | ✔ | Facultatif |
| `track follow-wallet` | L | **S** | **10** | **✘** | Facultatif (wallets suivis sur ton compte GMGN) |
| `callout record` / `global` | L | K | ? | ? | Plus tard |
| `gas-price` | L | K | 1 | ✔ | Calibrage priority/tip fee |
| `order quote` | L | **S** | **10** | **✘** | Simulation de vente (honeypot SOL, Q-005) |
| `order get` | L | **S** | 5 | ✔ (rafale 1) | **Suivi d'ordre** |
| `order strategy list` | L | **S** | 1 | ✔ | Réconciliation des TP/SL |
| `swap` | **E** | **S** | **10** | **✘** | **Execution Engine** |
| `multi-swap` | **E** | **S** | 10+ | **✘** | ⛔ interdit (mono-wallet) |
| `order strategy create` | **E** | **S** | 5 | ✔ (rafale 1) | TP/SL de secours |
| `order strategy cancel` | **E** | **S** | 2 | ✔ | Arrêt d'urgence |
| `cooking create` / `stats` | **E** / L | S / K | ? | ? | ⛔ création de tokens |
| `config` / `--check` / `--apply` | **C** | — | — | — | Mise en place des clés (voir ENVIRONMENT_SETUP §4) |

## 2. ⚠ Constat bloquant : le plan Free ne permet pas de trader

`docs/cli-usage.md` (Rate Limit Handling) : un poids supérieur à la capacité du plan renvoie **`403 PLAN_UPGRADE_REQUIRED`**, qui « ne peut jamais réussir sur ce plan ». Exemples cités : **Free + `swap`, `multi-swap`, `order quote`, `track follow-wallet`**.

Conséquences :
- **Phases 2 à 5** (lecture, analyse, paper trading) : faisables sur **Free**.
- **Phase 6 et au-delà** (exécution réelle) : il faut au minimum le plan **Plus** (capacité 20).
- **Coût du plan inconnu** (non publié dans la doc ; page `gmgn.ai/ai?tab=paid_plans`). Sur un capital de 50 $, un abonnement mensuel peut peser plus lourd que les frais de trading. → **Q-009**, à intégrer à l'évaluation économique de la Phase 5.

## 3. Débit en pratique (plan Free)

Débit soutenu ≈ `5 ÷ poids` requêtes/s ; rafale ≈ `floor(5 ÷ poids)`.

| Boucle type | Coût | Fréquence soutenable sur Free |
|---|---|---|
| 1 × `market trending` (3) + 1 × `market trenches` (2) | 5 | ~1 cycle/s au maximum théorique |
| + `token security` + `token pool` pour 5 candidats (10) | 15 | ~1 cycle / 3 s |
| + `token holders` sur 2 finalistes (10) | 25 | ~1 cycle / 5 s |

Conclusion : un **scan toutes les 15 à 30 s** laisse de la marge pour la surveillance des positions. Toute rafale non maîtrisée prolonge le bannissement (+5 s par requête, jusqu'à 5 min). Le régulateur de débit global est obligatoire (G-03).

## 4. Authentification

| Élément | Détail (source : `src/client/signer.ts`, `src/commands/config.ts`) |
|---|---|
| Clé API | Créée sur `gmgn.ai/ai` à partir d'une clé publique Ed25519 (ou RSA) |
| Signature | Message `{sous_chemin}:{query triée}:{corps}:{timestamp}`, Ed25519 brut, base64 |
| Anti-rejeu | `timestamp` valide à **±5 s**, `client_id` UUID refusé s'il est réutilisé sous 7 s → horloge synchronisée obligatoire |
| Stockage | `~/.config/gmgn/.env` (puis `.env` du dossier courant, prioritaire), variables d'environnement prioritaires sur les deux |
| `gmgn-cli config` | Génère `~/.config/gmgn/keypair.pem` (mode 600) et affiche le lien `https://gmgn.ai/ai/generateapi?pbk=<clé publique>` |
| `config --apply <key>` | Écrit `GMGN_API_KEY` + `GMGN_PRIVATE_KEY` dans `.env` et vérifie avec `track follow-wallet` (poids 10 → **sur Free la vérification peut échouer** et afficher « network fail », sans que ce soit une vraie erreur réseau) |
| Réseau | IPv4 uniquement ; whitelist IP pour le trading |
| Sanitisation | Les sorties sont nettoyées contre l'injection de prompt (`sanitize.ts`) |

## 5. Paramètres d'exécution (pour la Phase 6)

| Paramètre | Sens réel | Piège |
|---|---|---|
| `--amount` | Plus petite unité (lamports pour SOL, 1 SOL = 10⁹) | Ne jamais passer un montant « humain » |
| `--percent` | % du solde, **interdit** si l'entrée est SOL/USDC | — |
| `--slippage` | **Pourcentage** : `30` = 30 % | La démo passe `0.01` (bug B1) |
| `--min-output` | Sortie minimale garantie | Utile comme second garde-fou contre le slippage |
| `--anti-mev` | Protection MEV (défaut : activé) | À forcer explicitement |
| `--priority-fee` / `--tip-fee` | En SOL | **Obligatoires** sur SOL avec `--condition-orders` et `order strategy create` |
| `--condition-orders` | JSON : `profit_stop`, `loss_stop`, `profit_stop_trace`, `loss_stop_trace` (+ `drawdown_rate`) | Création *best-effort* : vérifier `strategy_order_id`. ⚠ Contradiction dans la doc : « champs supplémentaires → 400 », alors que les ordres trailing exigent `drawdown_rate` → à tester en Phase 6 |
| `--sell-ratio-type` | `buy_amount` (défaut) / `hold_amount` | — |
| Statuts `order get` | `pending` → `processed` → **`confirmed`** ; `failed` / `expired` | Seul `confirmed` (+ `report`) = exécuté |
| Limiteur d'erreurs | Répéter la même erreur métier sur `swap` (ex. `40003701` solde insuffisant) → `ERROR_RATE_LIMIT_BLOCKED` | Ne jamais réessayer sans corriger la cause |

## 6. Filtres serveur : un levier sous-exploité par la démo

`market trending` et `market trenches` acceptent des bornes côté serveur (`--max-rug-ratio`, `--max-bundler-rate`, `--max-top10-holder-rate`, `--min-liquidity`, `--max-created`, `--min-smart-degen-count`…). Pour plusieurs taux, la doc précise : **« tokens lacking this field are excluded »**. C'est exactement notre règle « donnée absente = rejet », appliquée gratuitement côté serveur.
→ Le scanner pré-filtrera côté serveur, puis **revérifiera côté client** (défense en profondeur, car un filtre serveur peut changer sans préavis).

## 7. Commandes interdites dans MemePilot

`multi-swap`, `cooking create`, `config --apply` (depuis le code), et toute commande d'exécution hors de `memepilot.execution`. Notre client lecture seule le garantit déjà par liste blanche (testé).
