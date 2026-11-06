# SPECIFICATION — MemePilot Auto V2

Notation : **DOIT** = obligatoire ; **DEVRAIT** = recommandé ; ID stables pour la traçabilité dans les tests.

## 1. Exigences fonctionnelles

### 1.1 Données de marché (lecture seule)
- **F-01** Le système DOIT découvrir des candidats via les commandes officielles uniquement :
  `market trending`, `market trenches`, `market signal`, `track smartmoney`, `track kol`.
- **F-02** Il DOIT enrichir chaque candidat avec `token info`, `token security`, `token pool`, `token holders`, `market kline`.
- **F-03** Chaque réponse API DOIT être validée par un modèle Pydantic ; une réponse invalide est rejetée et journalisée.
- **F-04** Chaque donnée DOIT porter un horodatage d'acquisition ; une donnée plus vieille que son TTL est considérée **périmée** (TTL configurables, ex. prix 10 s, sécurité 5 min).

### 1.2 Analyse et décision
- **F-10** Trois modules de signaux indépendants (momentum, smart money, early token) DOIVENT produire un `SignalReport` structuré.
- **F-11** Le Hybrid Strategy Agent DOIT produire un `TradeProposal` ou `NO_TRADE`, avec score, classe de risque et justification.
- **F-12** Une donnée manquante NE DOIT JAMAIS augmenter un score (elle est neutre ou pénalisante).
- **F-13** Les agents analytiques NE DOIVENT PAS avoir accès au client d'exécution (séparation au niveau du code et des imports).

### 1.3 Risk Guardian
- **F-20** Toute proposition DOIT passer le Risk Guardian, qui retourne `APPROVE` ou `REJECT(reasons[])`.
- **F-21** Contrôles obligatoires avant achat : filtres de sécurité, liquidité minimale, taille ≤ max, positions ouvertes < max, perte journalière, perte cumulée, absence d'ordre non résolu sur le même token, cohérence solde wallet ↔ ledger, fraîcheur des données, trades du jour < max.
- **F-21b** Budget de risque au pire cas (D-010) : un achat n'est approuvé que si `pertes_jour + exposition_ouverte + nouvelle_position ≤ limite_journalière` **et** `pertes_cumulées + exposition_ouverte + nouvelle_position ≤ limite_cumulée`. Les positions ouvertes et les ordres non résolus comptent pour 100 % de leur coût + frais. La taille est réduite au budget restant ; en dessous de `taille_min` → `NO_TRADE`.
- **F-22** Le Risk Guardian DOIT être du code déterministe pur (aucun appel LLM, aucun I/O réseau dans la fonction de décision).
- **F-23** Les limites DOIVENT être chargées au démarrage, validées, puis gelées (objet immuable). Aucune API interne ne permet de les modifier à chaud.
- **F-24** Atteinte de la perte cumulée → état `LOCKED` persistant ; seul un déverrouillage manuel hors processus (CLI admin + confirmation) peut le lever.

### 1.4 Exécution
- **F-30** L'Execution Engine est le **seul** module à appeler `swap`, `order strategy create/cancel`.
- **F-31** Chaque ordre DOIT avoir une clé d'idempotence locale enregistrée **avant** l'envoi.
- **F-32** Un accusé de réception API (`order_id`) NE DOIT PAS être interprété comme une exécution. Seul `status = confirmed` (+ `report` / montants remplis) vaut exécution.
- **F-33** Aucun nouvel ordre sur un token tant qu'un ordre précédent est `pending`/`processed`/inconnu.
- **F-34** Retries bornés : max 1 retry automatique, seulement si l'état précédent est prouvé `failed`/`expired`. Jamais après un `429` avant `X-RateLimit-Reset`.
- **F-35** Slippage plafonné par configuration ; `--anti-mev` activé.
- **F-36** Comptabiliser frais réseau, priority fee, tip fee et écart de prix (slippage réalisé).
- **F-37** Arrêt d'urgence : fichier/commande `HALT` qui bloque tout nouvel achat et, optionnellement, liquide les positions.
- **F-38** Au redémarrage : réconciliation ledger ↔ `portfolio holdings` ↔ `order strategy list` avant toute nouvelle décision.
- **F-39** Mesurer et journaliser : latence API (envoi → `order_id`) et latence de confirmation (envoi → `confirmed`).

### 1.5 Ledger et rapports
- **F-40** Ledger persistant append-only (SQLite) pour chaque décision, y compris `NO_TRADE` et `REJECT`.
- **F-41** Champs minimaux : timestamp, token, stratégie, signaux, score de risque, décision, paramètres d'ordre, résultat, frais, slippage, P&L réalisé/latent.
- **F-42** Rapport hebdomadaire calculé **uniquement** depuis le ledger et les données de portefeuille (capital début/fin, P&L net, win rate, nb trades, plus gros gain/perte, drawdown max, frais et slippage, performance par stratégie, alertes sécurité, transactions échouées).
- **F-43** Notifications Telegram : alertes temps réel (trade, rejet critique, LOCK, erreur) + rapport hebdo. Le bot est **sortant uniquement** au départ (aucune commande de trading via Telegram).

## 2. Exigences non fonctionnelles

- **NF-01** Code typé (mypy strict sur `risk/`, `execution/`, `ledger/`).
- **NF-02** Couverture de tests ≥ 90 % sur `risk/` et `execution/`.
- **NF-03** Logs structurés JSON, avec filtrage des secrets.
- **NF-04** Configuration par variables d'environnement + fichier YAML de limites ; `.env.example` avec placeholders uniquement.
- **NF-05** Respect des rate limits GMGN (lire `X-RateLimit-Reset`, arrêt total sur `RATE_LIMIT_BANNED_MAX`).
- **NF-06** Horloge synchronisée (NTP) : la signature GMGN est valide à ±5 s.
- **NF-07** Sortie réseau IPv4 uniquement.
- **NF-08** Aucun appel LLM dans les boucles de surveillance de prix.
- **NF-09** Modes d'exécution : `paper` (défaut), `live`. Le mode `live` exige un flag explicite + vérifications de démarrage réussies.

## 3. Hors périmètre (V2)

Autres chaînes que Solana, multi-wallet, levier, dépôts/retraits automatiques, création de tokens, commandes de trading via Telegram, interface web.
