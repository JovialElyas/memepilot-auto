# DECISIONS — MemePilot Auto V2

Format : ID · date · statut (ACCEPTÉE / PROPOSÉE / OUVERTE) · décision · raison.

## Décisions

**D-001 · 2026-10-31 · ACCEPTÉE** — Passage de MemePilot V1 (supervisé, approbation manuelle, 10 USD) à V2 autonome (50 USD, perte max 20 USD).
Raison : choix du propriétaire. Compensation : garde-fous déterministes, phases obligatoires, mode `paper` par défaut.

**D-002 · 2026-10-31 · ACCEPTÉE** — Source de vérité de l'intégration : dépôt officiel `GMGNAI/gmgn-skills` (commit `6a04e1f`, gmgn-cli 1.7.1) et `docs/cli-usage.md`. Aucun endpoint hors de ces sources.
Note : la page `docs.gmgn.ai/index/gmgn-agent-api` n'a pas pu être consultée pendant l'audit ; à relire en Phase 1 pour confirmer.

**D-003 · 2026-10-31 · ACCEPTÉE** — Intégration via wrapper Python de `gmgn-cli --raw` (option A), version épinglée 1.7.1. Mesures Phase 1 (depuis un serveur cloud, clé de démo) : démarrage Node ≈ 215 ms, appel complet ≈ 700–1 100 ms. Acceptable pour le scan (les sorties rapides sont confiées aux ordres conditionnels côté GMGN). Option B réévaluée en Phase 6 avec les latences mesurées depuis le VPS.
Bonus : gmgn-cli neutralise déjà les tentatives de prompt injection dans les métadonnées de tokens.

**D-004 · ACCEPTÉE** — Le Risk Guardian et l'Execution Engine sont 100 % déterministes ; aucun LLM dans le chemin d'exécution.

**D-005 · PROPOSÉE** — Sorties gérées en double couche : ordres conditionnels GMGN (persistants côté serveur) + surveillance locale.

**D-006 · PROPOSÉE** — SQLite en mode WAL, ledger append-only ; migration Postgres seulement si nécessaire.

**D-007 · PROPOSÉE** — Telegram en sortie uniquement (alertes + rapports) ; aucune commande de trading via Telegram en V2.

**D-008 · PROPOSÉE** — Le VPS doit avoir une IPv4 statique (whitelist IP GMGN, IPv6 non supporté).

**D-009 · 2026-10-31 · ACCEPTÉE** — Wallet d'exécution : le **wallet de trading intégré à GMGN** (cas B de `SECURITY.md` §4), alimenté **manuellement** par le propriétaire (50 USD max). Phantom ne sert qu'à se connecter à GMGN et à financer ce wallet ; il n'est jamais utilisé par le bot.
Reste à confirmer en Phase 1 : que ce wallet apparaît dans `gmgn-cli portfolio info` et qu'il est éligible au trading via la clé API. Sinon → STOP (cas C).

**D-010 · 2026-10-31 · ACCEPTÉE** — Résolution de Q-004 : **budget de risque au pire cas, positions ouvertes incluses.**
Hypothèse : toute position ouverte peut tomber à 0 (rug, gap sous le stop-loss). Sa perte potentielle = coût d'entrée + frais d'entrée + frais de sortie estimés.

Définitions :
- `exposition_ouverte` = Σ perte potentielle des positions ouvertes (+ ordres non résolus, comptés comme ouverts).
- `pertes_jour` = pertes réalisées nettes du jour, **frais inclus** (journée UTC).
- `pertes_cumulées` = pertes réalisées nettes depuis le démarrage live, **frais inclus** (les gains ne « rechargent » pas le budget cumulé : on compte le pire point atteint, voir ci-dessous).

Règle d'achat (Risk Guardian, les deux conditions sont obligatoires) :
```
pertes_jour      + exposition_ouverte + nouvelle_position ≤ 5 USD
pertes_cumulées  + exposition_ouverte + nouvelle_position ≤ 20 USD
```
Taille d'une nouvelle position :
```
taille = min(5, 5 − pertes_jour − exposition_ouverte, 20 − pertes_cumulées − exposition_ouverte)
si taille < taille_min (défaut 2 USD, frais trop lourds en dessous) → NO_TRADE
```
Conséquences pratiques :
- Au plus **5 USD à risque en même temps** : 1 position de 5 USD, ou 2 positions de 2,50 USD.
- Même si toutes les positions ouvertes font zéro, on ne dépasse **jamais** 5 USD de perte dans la journée ni 20 USD au total → au moins **30 USD sur 50 restent protégés** en toutes circonstances.
- `pertes_cumulées` est calculé sur la base du capital de départ (50 USD) : `max(0, 50 − capital_réalisé_minimum_atteint)`. Un gain après une perte ne permet pas de reprendre plus de risque que prévu.
- Quand `pertes_cumulées` atteint 20 USD → état `LOCKED` persistant (F-24).
- Quand `pertes_jour` atteint 5 USD → plus d'achats jusqu'au jour suivant ; les sorties restent autorisées.

**D-011 · 2026-10-31 · ACCEPTÉE** — Contrat de données (constats Phase 1 sur réponses réelles) :
- `rug_ratio`, `is_wash_trading`, `smart_degen_count`, `bundler_rate`, `rat_trader_amount_rate`, `sniper_count` ne sont **pas** dans `token security` : ils viennent de `market trending` / `market trenches`. `token info` donne `stat.*` (holders, bundlers, fresh wallets) sous d'autres noms (`top_bundler_trader_percentage`…).
- Les types varient : booléens en `1`/`true`, taux en nombre ou en texte (`"0.2485"`), enveloppe `{code,data}` pour certaines commandes seulement. → Modèles Pydantic tolérants à la coercition en Phase 2, avec la source de chaque champ documentée.
- Exemple observé : le token n°1 du trending SOL 1h avait `rug_ratio = 0.98` et `bundler_rate = 0.31` → rejeté par nos filtres.

**D-012 · 2026-10-31 · ACCEPTÉE** — Le code Python ne lit jamais `GMGN_API_KEY` ni `GMGN_PRIVATE_KEY` (test d'architecture). La clé de signature est générée localement par `scripts/generate_signing_key.py` et écrite directement dans `~/.config/gmgn/.env`. La clé API de Phase 1–5 est **lecture seule** (trading non activé).

**D-013 · 2026-10-31 · ACCEPTÉE** — Le mode `live` est refusé par le code (`Settings`) jusqu'à la Phase 7.

**D-014 · 2026-10-31 · ACCEPTÉE** — La démo GMGN AI Trader (`skillmarket-demos/aitrader`) n'est **ni forkée ni copiée** : pas de licence, humain dans la boucle par conception, défauts d'exécution (slippage 0,01, `processed` traité comme rempli, vente non confirmée, pas de TP/SL, risque en mémoire). On réimplémente 19 de ses idées dans notre architecture (voir `REUSE_VS_REBUILD.md`). Le code des skills `gmgn-skills` (MIT) peut être repris avec attribution.

## Incertitudes techniques et questions ouvertes

**Q-001 · PARTIELLEMENT RÉSOLUE (voir D-009)** — Compatibilité Phantom ↔ Agent API. Choix : wallet de trading GMGN. Vérification technique restante en Phase 1.
Constat : les swaps s'exécutent depuis le wallet « lié à la clé API », signés côté GMGN ; `GMGN_PRIVATE_KEY` n'est qu'une clé de signature de requêtes. GMGN ne peut pas signer avec une clé Phantom qu'il ne détient pas → hypothèse probable : un wallet de trading GMGN distinct. De plus, la doc est ambiguë : le `.env.example` dit que la clé « n'est PAS une clé de wallet », alors que `gmgn-swap/SKILL.md` dit qu'elle « doit correspondre au wallet lié à la clé API ».
Résolution : procédure `SECURITY.md` §4 en Phase 1.

**Q-002 · OUVERTE** — Modèle de garde du wallet de trading GMGN (qui détient la clé, comment retirer les fonds, que se passe-t-il si la clé API est révoquée).

**Q-003 · OUVERTE** — Coûts réels par aller-retour sur 5 USD (frais GMGN, priority fee, tip fee obligatoire avec `--condition-orders` sur SOL, slippage). Risque : coûts élevés en % sur de petites positions.

**Q-004 · RÉSOLUE (voir D-010)** — Cohérence des limites. Avec 2 positions × 5 USD et une perte journalière de 5 USD, deux rugs le même jour = 10 USD perdus, au-delà de la limite journalière (qui ne bloque que les *nouveaux* achats). Options :
  - (a) limite journalière sur pertes réalisées seulement (comportement actuel) ;
  - (b) compter l'exposition ouverte au pire cas : n'autoriser un achat que si `pertes_du_jour + exposition_ouverte + nouvelle_position ≤ 5 USD` → en pratique 1 position de 5 USD, ou 2 positions de 2,50 USD ;
  - (c) même règle (b) appliquée à la limite cumulée de 20 USD (recommandé dans tous les cas).
  Recommandation : (c) obligatoire + (b).

**Q-005 · OUVERTE** — Détection honeypot sur Solana : non fournie par GMGN. Vérifier si `order quote` (signé) permet de simuler une vente sans exécution.

**Q-006 · OUVERTE** — Plan tarifaire GMGN et rate limits réels pour une boucle de scan (certains endpoints peuvent renvoyer `403 PLAN_UPGRADE_REQUIRED`). Documenté : seau à fuite par plan — Free `5/5`, Plus `20/20`, Pro `50/50` (débit/capacité), chaque route a un poids (ex. `portfolio info` = 2). Sur Free : ~1 à 2 requêtes/s soutenues. Le scanner de Phase 2 aura un budget de requêtes explicite.

**Q-007 · OUVERTE** — Coût des appels LLM à l'exécution par rapport à un capital de 50 USD. Proposition : LLM seulement pour la revue hebdomadaire, budget plafonné.

**Q-009 · OUVERTE · BLOQUANTE pour Phase 6 · décision économique du propriétaire** — Plan GMGN : sur le plan Free (capacité 5), `swap` (poids 10), `order quote` (10) et `track follow-wallet` (10) renvoient toujours `403 PLAN_UPGRADE_REQUIRED` (`docs/cli-usage.md`). L'exécution exige au minimum le plan **Plus**.
Tarifs relevés sur `gmgn.ai/ai?tab=paid_plans` le 2026-10-31 :
| Plan | Prix | Capacité |
|---|---|---|
| Free | 0 $ — **exige un solde minimum de 100 $ sur le compte** | 5 |
| Plus | **29 $/mois** (offre limitée, 60 $ normalement) ou 5 000 pts/mois | 20 |
| Pro | 990 $/an (≈ 83 $/mois) ou 168 000 pts/an | 50 |
Analyse : un mois de Plus (29 $) coûte **plus que tout le budget de perte (20 $)** et 58 % du capital (50 $). Pour simplement couvrir l'abonnement, il faudrait +58 %/mois sur le capital, alors que D-010 limite le risque à 5 $ à la fois. Options à évaluer à la fin de la Phase 5 : (a) payer Plus seulement si le paper trading justifie l'espérance ; (b) payer avec des points GMGN (5 000 pts/mois, mode d'obtention à vérifier) ; (c) mode « signaux » : le bot décide, tu exécutes à la main sur GMGN (retour au modèle V1, aucun abonnement) ; (d) exécution hors GMGN (ex. agrégateur de swaps Solana) avec un hot wallet dédié plafonné → change le modèle de sécurité (le bot détiendrait une clé de wallet) : nécessite une nouvelle analyse de sécurité ; (e) arrêter au paper trading (projet portfolio).

**Q-010 · OUVERTE · à vérifier avant la Phase 2** — « Free tier requires a minimum account balance of $100 ». Avec un wallet alimenté à 50 $ max, l'accès API Free (même en lecture) pourrait être refusé. À vérifier : quel solde est compté (wallet de trading GMGN ? tous les wallets liés ?), et si la clé de démo publique suffit pour développer en attendant.

**D-015 · 2026-10-31 · ACCEPTÉE** — A-1 à A-4 d'`INTEGRATION_PLAN.md` approuvés : Phase 2 telle que décrite ; clés via le lien de `gmgn-cli config` + notre `--set-api-key` adapté pour lire `keypair.pem` ; plan Free jusqu'à la fin de la Phase 5 (sous réserve de Q-010) ; installation des skills GMGN pour Claude Code.

**Q-008 · OUVERTE** — Latence réelle : GMGN annonce < 0,3 s pour la soumission ; la confirmation on-chain est distincte et doit être mesurée en Phase 6.
