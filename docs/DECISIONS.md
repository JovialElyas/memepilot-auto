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

**D-016 · 2026-10-31 · ACCEPTÉE** — Phase 2 livrée (lecture seule) : régulateur de débit pondéré et gel global sur 429, modèles Pydantic (absent → `None`), fraîcheur `Fresh`, prix SOL/USD contrôlé, préfiltre de sécurité côté client + filtres serveur, scan SHADOW. Pièges réels documentés dans `GMGN_FIELD_MAP.md` §3 (token inexistant avec prix non nul, top10 = 0 non calculé, `--limit` des trenches ignoré, clé de démo partagée et bannie).

**D-017 · 2026-10-31 · ACCEPTÉE** — Pas d'architecture hybride multi-fournisseurs (DexScreener, RugCheck…) : trop de complexité et de compromis. **GMGN reste la seule source.** En attendant les fonds (B-01, B-02) : clé de démo publique, zéro dépense. Blocages et reprise : `BLOCKERS.md`.

**D-018 · 2026-10-31 · ACCEPTÉE** — Phase 3 livrée en mode papier : modules momentum / smart money / early token (fonctions pures, score + confiance + vetos), agrégation hybride, NO_TRADE par défaut, interface de test locale (`scripts/dashboard.py`). Correction issue des données réelles : le score agrégé est **escompté par la couverture des données** (un 88/100 obtenu avec des confiances de 0,2 et 0,4 valait en réalité ~27).

**D-019 · 2026-10-31 · ACCEPTÉE** — Phase 4 livrée : Risk Guardian pur (`risk/guardian.py`), état de risque immuable en `Decimal` (`risk/state.py`), persistance SQLite WAL (`risk/store.py`), administration manuelle (`scripts/risk_admin.py` : status / halt / resume / unlock). Règles : D-010 (pire cas, positions ET ordres en attente inclus), frais toujours comptés dans les pertes, taille arrondie au cent inférieur, refus si < 2 $. LOCK persistant à 20 $ de perte cumulée, levé uniquement avec une phrase tapée à la main (les pertes ne sont pas effacées). Limites modifiées : démarrage live refusé, état papier réinitialisé. Frais aller-retour estimés à **0,40 $** par défaut (hypothèse prudente, Q-003). Hypothèse des tests de propriétés : frais réels ≤ frais estimés.

**D-020 · 2026-11-01 · ACCEPTÉE** — Phase 5 livrée (papier) : moteur `engine/paper.py`, ledger SQLite (`data/paper.db` : décisions, ordres idempotents, fills, positions, équité, événements), courtier papier, sorties automatiques, Telegram sortant, rapport hebdomadaire calculé du ledger avec verdict économique face aux 29 $/mois.
Hypothèses de coûts (Q-003, à recalibrer) : 1 % de frais par côté + 0,05 $ réseau par transaction ; glissement 0,5 % + 2 × taille/liquidité ; ordre refusé au-delà de 30 %. Estimation présentée au Guardian : max(0,40 $, 1,5 × aller-retour). Simplifications papier : sorties évaluées une fois par cycle (pessimiste face aux ordres conditionnels serveur), pas de prise de profit partielle (trailing stop dès +50 %), un token sans prix pendant 5 cycles est vendu à 0 (pire cas). Bogue corrigé pendant les tests : la clé d'idempotence d'achat dépendait de la seconde courante ; elle utilise désormais un identifiant unique par cycle.

**D-021 · 2026-11-01 · PROPOSÉE (à valider)** — B-10 : profil **exploration** pour produire des données de test.
- Réservé au papier : `profile_config("exploration", "live")` lève `ProfileNotAllowed` (testé). Le live reste en profil strict.
- Exploration : seuil 55 (strict 70), 1 confirmation (strict 2), pondération momentum 45 % / smart money 25 % / early 30 % (strict 35/45/20). Mesure du 2026-11-01 : le flux smart money (200 trades pour tout Solana) ne voit presque jamais un token donné ; à 45 %, ce « non vu » plafonnait à ~49 des tokens momentum 84 / early 98.
- **Inchangés** : filtres de sécurité, vetos, Risk Guardian, limites, refus du risque HIGH, demi-taille en MEDIUM.
- Données : enrichissement `token info` des 6 meilleurs candidats négociables (variation 5 min / 1 h, achats/ventes 5 min, liquidité la plus faible) ; klines réservées aux candidats négociables ; dernier flux smart money réutilisé ≤ 180 s si la clé de démo est limitée.
- Chaque trade porte `profile` et `strict_ok` (le profil strict l'aurait-il pris ?). Le rapport hebdomadaire sépare les deux ; **le verdict économique n'utilise que les trades `strict_ok`**.
- Résultat mesuré : 0 → 1 achat papier sur 6 cycles réels (iTech, MEDIUM, 2,50 $).
- Corrections associées : `risk_admin.py` pilote désormais `data/paper.db` (l'état réel du bot papier) ; un HALT manuel posé pendant un cycle n'est plus écrasé par la sauvegarde de fin de cycle.

**D-022 · 2026-11-01 · PROPOSÉE (à valider)** — Vitesse, interface live, CI/CD.
- Deux boucles : scan toutes les 30 s (début à début ; appels GMGN indépendants en parallèle : cycle mesuré ≈ 6 s au lieu de ≈ 10–15 s) et surveillance des positions toutes les 5 s (1 appel de poids 1 par position). Un verrou empêche les deux boucles d'agir en même temps. Le seuil « prix absent » est converti en durée (≈ 5 min) ; une limitation de débit n'est jamais comptée comme un prix absent.
- Pendant un gel 429 : échec immédiat des appels et attente de la fin du gel avant le cycle suivant.
- Limite physique : chaque appel = un processus `gmgn-cli` (≈ 0,75 s). La clé de démo partagée est bannie fréquemment ; le vrai gain de vitesse viendra d'une clé personnelle (B-01) puis du plan Plus (Q-009). Remplacer `gmgn-cli` par des appels HTTP directs serait possible plus tard (moins de latence) mais change l'audit de sécurité : non fait.
- Interface live (127.0.0.1:8765, lecture seule, sans dépendance) inspirée visuellement de la démo GMGN (aucun code copié : la démo n'a pas de licence).
- CI/CD GitHub Actions : tests sur 3 versions de Python + essai hors ligne + gitleaks ; déploiement sur `main` seulement, via une clé SSH à commande forcée, tests rejoués sur le serveur, bascule atomique, contrôle de santé vérifiant la version servie, retour arrière automatique. Simulé avec succès (4 scénarios).

**D-023 · 2026-11-01 · ACCEPTÉE (demande du propriétaire)** — **Pas d'hébergement ni de déploiement tant que le papier n'est pas rentable.** Le bot tourne sur le PC (lanceur `run_forever.ps1`, démarrage automatique facultatif, veille Windows suspendue pendant l'exécution). Le VPS RackNerd (≈ 22 $/an) reste l'option retenue pour plus tard ; la CI/CD est prête mais inactive (`DEPLOY_ENABLED` absent). La solution d'accès GMGN (solde de 100 $, plan Plus) est reportée.
Porte de rentabilité, en trois temps (voir ROADMAP « Prochaines étapes ») :
1. Collecte : ≥ 50 trades papier fermés (profil exploration).
2. Calibration hors ligne sur ces trades : quelles conditions d'entrée / de sortie gagnent ? → profil « v2 » figé.
3. Validation : ≥ 50 NOUVEAUX trades papier avec le profil v2, jamais vus pendant la calibration (sinon on s'adapte au hasard). Rentable = espérance par trade > 0 après frais, facteur de profit > 1,2, aucune limite de risque touchée.
Constat économique honnête : avec 50 $ de capital et 5 $ par position, couvrir Plus (29 $/mois) + VPS (≈ 1,83 $/mois) exige ≈ +62 %/mois. Une stratégie rentable en papier ne suffira donc probablement pas à justifier Plus ; la décision live examinera aussi le mode « signaux » (Q-009 c : le bot décide, exécution manuelle, sans abonnement).

**D-024 · 2026-11-01 · ACCEPTÉE (demande du propriétaire)** — **Relais de données gratuit** quand GMGN bloque (révise D-017 de façon limitée : GMGN reste la source prioritaire).
- Sources : DexScreener (prix, variations 5 min / 1 h, achats/ventes 5 min, liquidité, pool), GeckoTerminal (tendances et nouveaux pools, en alternance : 1 liste par cycle, ~10 req/min mesurées au lieu des 30 annoncées), RugCheck (autorités mint/freeze, top 10 hors pools et lockers, part du dev, insiders, risques). Sans clé, HTTPS, 3 hôtes autorisés seulement, lecture seule.
- Architecture : `relay/translate.py` (traductions pures vers le format GMGN), `relay/client.py` (rythme et gel par hôte, caches), `relay/router.py` (`--source auto|gmgn|relay`, défaut `auto`). Le reste du bot (modèles, préfiltre, signaux, Guardian, interface) est inchangé et revalide tout.
- Approximations documentées : `rug_ratio` = 1 si RugCheck dit « rugged » ou risque « danger », sinon 0 ; `is_wash_trading` = moins de 15 % de wallets distincts pour ≥ 50 transactions/h. Toute donnée absente reste absente → rejet par le préfiltre.
- Ce qui manque en relais : flux smart money / KOL (le module smart money réutilise le dernier flux GMGN ≤ 180 s, sinon se met en retrait) et bougies 1 min (quota réservé aux listes).
- Garde-fous ajoutés après un incident réel en test (NIKETYSON : pair DexScreener sans liquidité, prix faux d'un facteur 10⁶ → stop-loss « −100 % » fictif) : pairs < 1 000 $ de liquidité ignorées ; toute chute > 50 % entre deux contrôles doit être confirmée au contrôle suivant avant une vente (valable aussi pour les prix GMGN).
- Chaque position enregistre `data_source` (gmgn / relais / mixte) pour la calibration.
- Mesure (clé de démo, 2026-11-01) : 10 cycles complets sur 10, 0 bloqué, 41 requêtes servies par le relais.
- Nouveau départ du compteur de 50 trades (`paper_trade.py --reset`), le changement de source modifiant les signaux.
- Autres corrections : la vérification « un seul bot » se fait avant toute écriture (un `--fresh` lancé par erreur pendant qu'un bot tourne ne peut plus corrompre sa base) ; l'archivage déplace aussi les fichiers `-wal`/`-shm`.

**D-025 · 2026-11-01 · ACCEPTÉE (demande du propriétaire : « qu'il place enfin des trades »)** — Le score d'exploration n'est plus escompté par la couverture des données.
- Constat sur le bot du propriétaire (82 cycles complets en 41 min, 0 trade) : quand GMGN est gelé, pas de bougies ni de flux smart money → confiances plafonnées à 0,6 / 0,4 / 0,5. Avec l'escompte, le score maximal atteignable était **52 < seuil 55** : aucun trade possible, même sur un token parfait (METT : ~90 brut → 46,7 affiché).
- Exploration : score brut pondéré par la confiance, **couverture minimale 50 %** (le cas MOGZ, confiances 0,2 / 0,4, reste refusé), au moins un module confirmant (≥ 55 avec confiance ≥ 0,5), vetos, sécurité, refus HIGH et Guardian inchangés. Profil strict (live) inchangé.
- Bougies 1 min via le relais (GeckoTerminal) pour le meilleur candidat négociable : 1 série par cycle, aucune pendant 10 min après un 429 (les listes d'abord). Elles rétablissent la confiance du momentum et les vetos « pompe verticale » / « mèche de rejet » avant un achat.
- Rejeu sur le dernier cycle réel du propriétaire : 1 proposition (BNB, MEDIUM, 59,8), JUGGERNAUT refusé (couverture 25 %).
- Le compteur de 50 trades repart de ce changement (0 trade depuis le --reset : rien n'est perdu).

**D-026 · 2026-11-01 · ACCEPTÉE (propriétaire)** — Sorties plus serrées, veto « achat de sommet », prix enregistrés.
- Constat réel (niketyson) : position à **+33 %** à 2,5 min, puis **−1,8 %** 40 s plus tard. Le bot avait vu le +33 % (surveillance 5 s) mais aucune règle n'agissait entre +10 % et +50 % (trailing armé à +50 %). Ce n'était pas la vitesse des requêtes, c'était la règle de sortie.
- Sorties : trailing armé dès **+15 %**, vente à **−10 % sous le sommet** (avant +50 % / −20 %). Stop-loss −25 %, time-stop 30 min (si gain < +10 % et trailing non armé), durée max 240 min inchangés.
- Entrées : veto momentum « achat de sommet » quand le token a déjà fait **≥ +100 % sur 1 h ET ≥ +20 % sur 5 min**, sans besoin de bougies (niketyson avait été acheté à +23 % / +175 %).
- Mesure : table `price_ticks` — prix de chaque position toutes les ~5 s, **et 30 min après la vente** (sinon une règle plus lâche ne pourrait pas être jugée). `scripts/replay_exits.py` rejoue une grille de règles de sortie sur ces prix réels, avec le même modèle de coûts (≥ 30 trades conseillé avant de conclure).
- Choix de principe (pas d'ajustement sur un seul trade) : un trailing à +50 % est inadapté à des memecoins tenus 30 min. Les seuils exacts (+15 % / −10 %) seront revus avec `replay_exits.py` à la calibration.
- Compteur de 50 trades remis à zéro (`--reset`).

**D-027 · 2026-11-01 · ACCEPTÉE (propriétaire : « tout mettre en place maintenant, ajuster ensuite »)** — Trois règles d'achat supplémentaires.
- **Pause de 2 h** sur un token après une sortie perdante (cas niketyson, racheté plusieurs fois). Refus motivé, visible dans l'interface.
- **Tendances 5 min** (skill GMGN « 5-Min Trending ») en complément des tendances 1 h. Liste facultative : si elle manque (GMGN gelé ; le relais n'a pas d'équivalent 5 min, il refuse plutôt que de renvoyer du 1 h mal étiqueté), le cycle continue. Les candidats portent la source `trending:5m`.
- **Position supplémentaire seulement « sous contrôle »** : chaque position déjà ouverte doit avoir un prix confirmé il y a moins de 20 s et ne pas perdre plus de 10 %. Conséquence : au plus un achat par cycle (la position qui vient d'être achetée n'a pas encore de prix confirmé). Le Guardian garde son veto et ses limites.
- Constat au premier essai : la 2ᵉ position est en pratique bloquée par le **budget de risque du jour** (D-010 : une position ouverte compte comme perdue à 100 % + frais). Avec 5 $/jour, une position de 2,50 $ consomme 2,90 $ ; il reste 2,10 $ < 2 $ + 0,40 $ de frais. Ouvrir 2 positions demanderait de changer D-010 ou les limites : décision du propriétaire, non prise.
- Compteur de 50 trades : nouveau départ conseillé (`--reset`).

**D-028 · 2026-11-01 · ACCEPTÉE (propriétaire)** — **Limite de perte quotidienne : 5 $ → 10 $** (`config/limits.yaml`), pour permettre 2 positions simultanées (option b de la discussion D-027, montée à 10 $ par le propriétaire).
- Inchangés : perte cumulée maximale **20 $** (plafond codé en dur, LOCK), 5 $ max par position (2,50 $ en risque MEDIUM), 2 positions max, 10 trades/jour, comptage au pire cas des positions ouvertes (D-010), règle « sous contrôle » (D-027).
- Effet : avec deux positions à demi-taille ouvertes (2 × 2,90 $), il reste 4,20 $ de budget du jour. Une journée très mauvaise peut maintenant coûter jusqu'à 10 $, soit la moitié du budget total de 20 $ : deux mauvaises journées suffisent à déclencher le LOCK.
- Tests : les tests de logique du Guardian utilisent désormais une copie figée des limites (`tests/fixtures/limits_test.yaml`, 5 $/jour) ; un test dédié vérifie la config réelle (2 positions acceptées, 3ᵉ refusée).
- En mode papier, le changement d'empreinte des limites réinitialise l'état de risque (prévu) ; nouveau départ du compteur par `--reset`.

**D-029 · 2026-11-01 11 h (heure de l'Atlantique) · ACCEPTÉE (propriétaire : « corriger le choix du trade pour ne pas entrer quand c'est en hausse »)** — Ne plus acheter en plein pic.
- Analyse des 10 premiers achats réels (profil exploration, D-021 à D-028) : 0 gagnant sur 9 fermés. Les 6 achats faits pendant un pic (bougies +15 à +23 % sur les dernières minutes, ou +11 à +23 % sur 5 min, ou +142 % sur 1 h) n'ont jamais dépassé +5 % ensuite. Les 3 qui sont montés (+15 %, +23 %, +50 %) avaient été achetés au calme (accélération des bougies −2 à +5 %, 0 à +7 % sur 5 min).
- Nouveaux vetos du module momentum : variation 5 min ≥ **+12 %** ; accélération des bougies > **+10 %** ; variation 1 h ≥ **+100 %** (seule, sans condition sur 5 min). Rejouées sur les 10 achats : les 6 achats de pic sont refusés, les 3 qui sont montés passent (test automatique).
- **Prudence** : règle tirée de 10 trades seulement. Elle sera jugée sur les trades suivants (positions ouvertes après cette décision), pas sur ceux qui ont servi à la construire.
- Pas de remise à zéro (la position AODF en cours est conservée) : à la revue, on compare les trades ouverts avant et après D-029.

**D-030 · 2026-11-01 12 h (heure de l'Atlantique) · ACCEPTÉE (propriétaire : « optimiser le temps de réactivité », « vas-y »)** — Entrées confirmées, sorties plus rapides, gains protégés.
- Constat (3 559 prix enregistrés) : baisses jusqu'à −19,5 % en 6 s ; stops à −25 % exécutés entre −26 % et −32 %. Trou dans les règles : trailing armé à +15 % et vente à −10 % du sommet = sortie vers +3,5 %, soit une perte après frais (aller-retour ≈ 6,5 % sur 2,50 $).
- **Achat confirmé** : décision → réservation du budget → nouveau prix ~5 s après. Achat seulement si le prix n'a ni chuté de ≥ 5 % ni monté de ≥ 8 % (pic) ; sinon annulation (ordre CANCELLED, réservation libérée, aucun trade compté). Délai max 30 s.
- **Plancher de gain** : une fois +15 % atteint, jamais de sortie sous **+8 %** (frais couverts + marge).
- **Trailing serré** : au-delà de +40 % de sommet, vente à **−7 %** du sommet (−10 % en dessous).
- **Prise de profit partielle** : **la moitié vendue à +50 %**, une seule fois ; le reste suit le trailing. Ligne fermée séparée dans le ledger (`prise de profit partielle…`), refondue dans sa position pour le rejeu. Les rapports la comptent comme un trade.
- **Réactivité** : surveillance toutes les **2 s** (au lieu de 5 s ; cache prix du relais ramené à 1 s) ; chute > 50 % revérifiée **~1 s** plus tard au lieu du contrôle suivant.
- **Papier plus honnête** : une vente s'exécute au **pire** des deux prix vus à ~1 s d'écart (délai d'une vraie transaction).
- Limites : aucun réglage ne protège d'un effondrement total en moins de 2 s ; la petite mise reste la protection principale. En réel (Phase 6), les stops devront être des ordres conditionnels côté GMGN.
- Pas de remise à zéro : AODF (+58 %) garde son historique ; elle vendra sa moitié au premier contrôle après le redémarrage si elle est encore ≥ +50 %.

**D-031 · 2026-11-01 12 h 10 (heure de l'Atlantique) · ACCEPTÉE (propriétaire)** — Critère « le papier est rentable ».
- **Gagné** si, sur **30 trades fermés** depuis l'entrée en vigueur de D-030 (2026-11-01 16:00 UTC), le **P&L net frais compris est positif**, **ou** si **au moins 75 %** des trades sont gagnants.
- Une vente partielle compte avec sa position (1 position = 1 trade). Au-delà de 30, on juge les 30 derniers.
- Affiché par `scripts/goal_status.py`, le rapport hebdomadaire et Telegram. Prérequis (D-023) à toute discussion d'hébergement ou de réel ; ne remplace pas les contrôles de sécurité de la Phase 6.

**D-032 · 2026-11-01 12 h 25 (heure de l'Atlantique) · ACCEPTÉE (propriétaire : « monte la limite de trade à 100 mais ne change pas les limites de pertes »)** — **Trades par jour : 10 → 100** (`config/limits.yaml`, maximum autorisé par le validateur).
- **Inchangés** : perte du jour 10 $, perte cumulée 20 $ (LOCK, plafond codé en dur), 5 $ max par position, 2 positions max, budget de risque au pire cas (D-010), règle « sous contrôle » (D-027).
- Ce sont désormais les limites de **pertes** qui arrêtent le bot, pas le nombre de trades. Plus de trades = plus de frais : chaque aller-retour coûte ≈ 0,16 $ ; le critère D-031 les compte.
- Correctif lié : en papier, un changement de limites **conserve** l'état de risque (positions ouvertes, capital réalisé, pertes déjà consommées) au lieu de le réinitialiser — sinon AODF aurait déclenché un HALT et les 5,53 $ de pertes cumulées auraient été effacés. Réinitialisation seulement si le capital initial change. En live, tout changement reste refusé.

**D-033 · 2026-11-01 12 h 47 (heure de l'Atlantique) · ACCEPTÉE (propriétaire, après revue pour/contre)** — **5 $ par position, perte du jour 15 $, perte totale 20 $ inchangée.**
- Demande initiale : 20 $/jour et 35 $ au total. Revue : 35 $ refusé sur recommandation (règle fondatrice « 20 $ immuable », plafond codé en dur, et le papier doit tester les règles du réel ; 35 $ = 70 % du capital). 20 $/jour = 20 $ au total : une seule journée pourrait déclencher le LOCK → 15 $ retenu. Le propriétaire a validé.
- `min_position_usd` 2 → **5**, `max_position_usd` **5**, `daily_loss_limit_usd` 10 → **15** ; `maximum_cumulative_loss_usd` **20** (inchangé). Taille pleine aussi en risque MEDIUM (`HybridConfig.medium_size = 1.0`, avant 0,5).
- Effet : frais aller-retour ≈ 4 % au lieu de 6,4 %. Pire cas réservé ≈ 5,40 $ par position : 2 positions = 10,80 $ ≤ 15 $. Un stop-loss coûte ≈ 1,70 $ au lieu de 0,85 $.
- Nouvelle session papier (`--reset`) prévue après la fermeture d'AODF, pour lancer le test D-031 (30 trades) avec le budget de 20 $ complet.
- Observation du jour : Anakin vendu par le plancher de gain (sommet +17 %, sortie à +6 % après saut de prix) → −0,04 $ au lieu d'une perte habituelle ; le plancher limite la casse mais un saut de prix peut l'enjamber.

**D-034 · 2026-11-01 13 h 10 (heure de l'Atlantique) · CORRECTIF** — Cas AODF et surveillance aveugle pendant les scans.
- AODF : moitié vendue à +61 % (+0,64 $) ; le reste est monté à +94 %, puis **effondrement en 2 s** (12:53:14 → 12:53:16 : +94 % → −91 %, liquidité 2,63 M$ → 0,89 M$, puis −98 %). Vente au premier contrôle à −92 % (−1,24 $). Aucun prix intermédiaire n'existait : aucune règle de sortie par surveillance ne pouvait l'éviter. Bilan AODF −0,60 $ au lieu de ≈ −2,48 $ sans la vente partielle.
- Défaut trouvé en analysant : le scan tenait le verrou pendant toute l'analyse (7 à 40 s de réseau) → surveillance des positions bloquée, trous de prix jusqu'à 62 s. Correctif : verrou seulement pour les sorties et les achats ; l'état de risque est relu avant les achats. Test : la surveillance vend pendant qu'un scan analyse.
- Pistes contre les effondrements (non appliquées, à décider) : prise de profit en paliers, durée max réduite pour les gros gains, critères de sécurité supplémentaires à l'entrée.

**D-035 · 2026-11-01 13 h 20 (heure de l'Atlantique) · ACCEPTÉE (propriétaire : « le but c'est d'avoir beaucoup de win trades, pas de garder un coin qui s'effondre »)** — **Prise de profit TOTALE à +12 %.**
- Rejeu sur les 11 trades réels (prix enregistrés, mise ramenée à 5 $) : règles D-030 −11,26 $ (1 gagnant) ; TP +8 % −8,62 $ ; TP +10 % −7,86 $ ; **TP +12 % −7,28 $ (4 gagnants)** ; TP +15 % −7,73 $ ; TP +20 % −7,91 $ ; TP +30 % −8,47 $. Les pertes restantes viennent des achats en plein pic, déjà refusés par D-029.
- Time-stop raccourci (15 min, 5 min) : résultats contradictoires sur 4 trades seulement → time-stop 30 min conservé, à revoir à la revue (variantes dans `replay_exits.py`).
- +12 % brut ≈ +8 % net après frais (≈ +0,40 $ par trade de 5 $). Le stop-loss reste prioritaire ; trailing, plancher et moitié à +50 % restent en secours (saut de prix au-dessus du seuil = vente totale quand même).
- Contrepartie assumée : plus de « +94 % » gardé. Risque de surajustement (11 trades) : à valider sur les 30 trades de D-031.
- Option 3 (skill GMGN de sécurité) : `gmgn-contract-dd` (score 0-100 : détention créateur/équipe, LP brûlée/verrouillée, snipers, bundlers, nouveaux wallets, étiquette rug GMGN) et le guide `workflow-risk-warning` existent. Nos filtres couvrent déjà la plupart de ces champs ; manquent LP brûlée/verrouillée et l'étiquette rug. Non intégré : AODF (2,6 M$ de liquidité, 15 h d'âge) les aurait passés ; un effondrement de 2 s ne se voit pas à l'entrée.

**D-036 · 2026-11-01 14 h 25 (heure de l'Atlantique) · CORRECTIF** — Flux smart money presque toujours gelé.
- Cause : quota GMGN Free (5/s, utilisé à 4/s) partagé entre le scan (rafale), la surveillance des positions toutes les 2 s (~1 req/s depuis D-030), les confirmations d'achat et le suivi après vente. Le flux smart money/KOL était lu APRÈS la rafale du scan, donc tombait presque toujours sur un gel (« gel en cours »), et le relais gratuit ne fournit pas ces données → module smart money à vide (« trades SM lus 0 »).
- Correctifs : (1) prix des positions par le relais DexScreener d'abord (300 req/min), GMGN seulement en secours ; (2) flux smart money + KOL lu AVANT le scan ; (3) rafale du limiteur plafonnée à 4 (sous-processus gmgn-cli qui arrivent groupés) ; (4) dernier flux réussi réutilisable 10 min au lieu de 3 (la demi-vie de 10 min pondère déjà l'ancienneté).
- Aucune règle de trading ni limite de risque modifiée.

**D-037 · 2026-11-01 14 h 45 (heure de l'Atlantique) · ACCEPTÉE (propriétaire : « commençons par 1 et 2 »)** — Suivi des refus + chien de garde. Aucune règle de trading modifiée.
- **Refus suivis** : achats annulés à la confirmation, refus du Guardian, refus du contrôle (pause après perte, positions), refus « pic en cours / achat de sommet » (D-029) et candidats juste sous le seuil (score ≥ 45, sans veto). Prix relevé toutes les 15 s pendant 30 min (relais d'abord, 2 relevés max par contrôle, 20 tokens max). `scripts/review_refusals.py` et le rapport hebdomadaire les rejouent avec les règles de sortie actuelles (mise 5 $, frais) : refus UTILE si l'achat aurait perdu.
- **Chien de garde** : battement toutes les 30 s dans `data/heartbeat.json`. Telegram : arrêt détecté au redémarrage (> 2 min), veille du PC (saut d'horloge), aucun cycle complet depuis 5 min, positions non surveillées depuis 1 min, puis « reprise ». Optionnel : `MEMEPILOT_HEALTHCHECK_URL` (HTTPS) pingé tant que les cycles tournent, pour être prévenu par un service externe quand le PC est éteint.

**D-038 · 2026-11-01 15 h (heure de l'Atlantique) · ACCEPTÉE (propriétaire : « fais la correction »)** — Smart money = bonus seulement.
- Constat après D-036 (flux smart money rétabli) : un token sans achat smart money récent recevait 0 dans ce module, avec une confiance de 0,8, ce qui tirait la note globale vers le bas (Leverhedge : 54 < 55 ; ≈ 72 sans ce 0). Le blocage principal des 15 premières minutes restait la règle anti-pic D-029 (15 refus sur 16).
- Désormais : 0 wallet suivi détenteur, aucun achat récent ou flux coupé → le module s'abstient (neutre, ne compte pas dans la moyenne). Détenteurs et achats smart money → bonus comme avant. Revente par la smart money → veto conservé (y compris vendeurs sans aucun acheteur).
- Effet secondaire : le profil strict (2 confirmations) obtient moins souvent sa 2ᵉ confirmation ; il ne sert qu'à l'étiquette `strict_ok` en papier.

**D-039 · 2026-11-01 16 h (heure de l'Atlantique) · OBSERVATION** — Mesurer le coût de la limite de 2 positions.
- Le propriétaire regrette les propositions manquées faute de place. Plutôt que d'augmenter le risque (4 positions et 40 $ de perte totale écartés après discussion, 2026-11-01 15 h 47), les refus « 2 position(s)/ordre(s) ≥ max 2 » sont suivis à part (catégorie « places pleines ») dans `review_refusals.py`.
- Décision à la revue des 30 trades : si les « places pleines » auraient gagné nettement, envisager une 3ᵉ position dans les 20 $ de perte totale.

**D-040 · 2026-11-01 16 h 30 (heure de l'Atlantique) · ACCEPTÉE (propriétaire : « réduis la durée à 5 min, redéfinis les stratégies par rapport à ce temps avant d'appliquer »)** — Stratégie « 5 minutes ».
- Rejeu (mise 5 $, frais) sur les 16 trades réels (2 sessions) et sur les refus suivis depuis 14 h 42 :

| Règle | 16 trades réels | Gagnants |
|---|---|---|
| D-035 : +12 %, stop −25 %, time-stop 30 min | −9,80 $ | 5/16 |
| 5 min, +5 %, −15 % | −5,10 $ | 6/16 |
| **5 min, +8 %, −15 % (retenue)** | **−5,08 $** | 4/16 |
| 5 min, +12 %, −15 % | −4,61 $ | 3/16 |
| 10 min, +12 %, −15 % | −4,64 $ | 5/16 |

- Retenu : **durée max 5 min** (vente quoi qu'il arrive), **vente totale à +8 %** (≈ +4 % net, +0,20 $ ; +5 % ne laisserait que ≈ +1 % net), **stop-loss −15 %** (adapté à 5 min ; −10 % se fait déclencher par le bruit). Entrées inchangées. Trailing, plancher, moitié et chute de liquidité restent en secours. `CURRENT_EXITS` dans `paper/exits.py`.
- Constat honnête : aucune variante n'est rentable sur les 16 trades réels ; la durée courte réduit les pertes de moitié mais ne règle pas tout. Le problème principal reste la qualité des entrées face à ≈ 4 % de frais aller-retour.
- Objectif D-031 recompté à partir de D-040 (`GOAL_SINCE_TS` = 2026-11-01 20:30 UTC) : 30 trades avec les mêmes règles.
- Correctif du bilan des refus : un relevé sans prix (token absent du relais, ≈ 1/3 des relevés) était compté comme une chute à zéro après 5 relevés vides → pertes fictives (−48 $ sur 27 refus « sous le seuil »). Ces relevés sont maintenant ignorés.

**D-041 · 2026-11-02 6 h (heure de l'Atlantique) · ACCEPTÉE (propriétaire : « observer sans trader » + « tester l'hypothèse momentum très fort »)** — Fin de la session papier, laboratoire.
- Bilan de la session du 2026-11-01 14 h 42 : capital 34,91 $ (−15,09 $, 39 trades). Objectif D-031 ❌ (−11,83 $ sur 30, 37 % gagnants). Budget cumulé restant 4,91 $ < 5,40 $ : plus aucun achat possible depuis 01 h 14. Les règles D-040 n'ont jamais tourné (bot non redémarré).
- Constats : gain moyen +0,49 $ contre perte moyenne −0,94 $ (il faudrait 66 % de gagnants) ; frais 6,17 $ sur 31 trades, mais −7,25 $ même sans frais ; sur 374 refus suivis, la note globale prédit le MOUVEMENT (+8 % atteint en 10 min : 27 % → 50-75 % avec la note), pas la DIRECTION (moyenne ≈ 0 $/trade). Seule piste : momentum ≥ 80 (23 cas, +0,43 $/trade) — échantillon trop petit.
- Méthode changée : les règles ont été modifiées ~12 fois en 24 h sur 10 à 16 trades. Désormais : observation de masse, hypothèse enregistrée AVANT, validation sur deux moitiés de la période.
- `--observe` : aucun achat ; chaque candidat analysé suivi 15 min, prix toutes les 5 s par lots DexScreener (30 tokens par requête, ≤ 150 suivis), caractéristiques stockées (`shadow.kind = 'labo'`, colonne `features`). `scripts/lab_report.py` : achat virtuel 5 $ au premier prix relevé (~5 s après la décision, comme la confirmation), sortie selon `CURRENT_EXITS` (5 min, +8 %, −15 %).
- **H1 enregistrée : signal momentum ≥ 80.** Validée seulement si rentable sur les deux moitiés chronologiques, avec ≥ 30 cas par moitié. Variantes indicatives (sans le veto pic, liquidité < 50 k$) et repères (tous, PROPOSE).
- Restent à corriger avant la prochaine session papier : « données périmées » vérifiées par cycle au lieu de par token ; tokens absents de DexScreener.

**D-042 · 2026-11-02 19 h 30 (heure de l'Atlantique) · CORRECTIF + POINT D'ÉTAPE du laboratoire**
- Prix aberrants : DexScreener bascule parfois sur une autre pair (WWIII : ×42 pendant 30 s, liquidité 12 k$ → 298 k$, puis retour). Un seul cas donnait +200 $ virtuels. `drop_glitches` ignore tout saut ×3 (ou ÷3) qui revient au prix d'avant en moins de 2 min ; les vraies chutes ou envolées qui durent sont conservées.
- Point après 13 h (952 candidats avec prix) : tous les candidats −0,24 $/trade (REJETÉ) ; règle PROPOSE −0,31 $/trade (50 cas) ; **H1 (momentum ≥ 80) : 37 cas, −2,92 $, 56-63 % de gagnants** mais gain moyen +0,55 $ contre perte moyenne −1,00 $ (il faudrait ≈ 65 % de gagnants). Variantes de sortie (stop −8 %, +15 %, 15 min) : pires. Verdict en attente (30 cas par moitié).
- Bot arrêté ≈ 8 h 20 → 11 h et ≈ 14 h 30 → 16 h (aucun cycle) : PC en veille ou fermé.

**D-043 · 2026-11-02 19 h 45 (heure de l'Atlantique) · ACCEPTÉE (propriétaire : « test H2 »)** — Abandon du scalping 5 min, hypothèse H2 « trend follower ».
- Recherche sur 952 candidats observés : 117 stratégies (9 filtres d'achat × 13 sorties, dont des sorties « trend follower » sur 15 min), choisies sur la 1re moitié et vérifiées sur la 2e. **Aucune n'est rentable, même sur une seule moitié** (meilleure : −0,02 $/trade puis −0,25 $). Sur 5 à 15 min, le prix bouge quasi au hasard et ≈ 0,20 $ de frais par aller-retour font perdre.
- **H2 enregistrée avant les données** : achat virtuel si momentum ≥ 60 ET token âgé d'au moins 6 h ; sortie : stop −20 %, puis une fois +20 % atteint, vente à −25 % sous le sommet ; 4 h max (`H2_EXITS`). Idée : peu de gagnants mais gros (style CTA / Man AHL), tokens ayant déjà survécu aux premières heures (moins de rugs).
- Laboratoire étendu : suivi de 4 h (toutes les 5 s pendant 15 min, puis toutes les minutes), jusqu'à 600 tokens, ≈ 30 requêtes DexScreener/min ; reprise des suivis en cours après un redémarrage.
- Évaluation sans biais : un candidat n'est jugé que si ses prix couvrent toute la durée de détention possible (sinon seuls les perdants, stoppés tôt, auraient une sortie).
- Verdict H2 : rentable sur les deux moitiés avec ≥ 30 cas chacune. Si H2 échoue, conclusion à documenter : pas d'avantage exploitable pour un petit bot sans vitesse ni données privilégiées.

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

**Q-010 · CONFIRMÉE 2026-10-31 · BLOQUANTE pour toute clé API personnelle** — La création d'une clé API (même Free, lecture seule) exige 100 $ de solde sur le compte GMGN ; le propriétaire ne les a pas pour l'instant. En attendant : développement sur la clé de démo publique (lecture seule, partagée, 429 fréquents). Texte initial : « Free tier requires a minimum account balance of $100 ». Avec un wallet alimenté à 50 $ max, l'accès API Free (même en lecture) pourrait être refusé. À vérifier : quel solde est compté (wallet de trading GMGN ? tous les wallets liés ?), et si la clé de démo publique suffit pour développer en attendant.

**D-015 · 2026-10-31 · ACCEPTÉE** — A-1 à A-4 d'`INTEGRATION_PLAN.md` approuvés : Phase 2 telle que décrite ; clés via le lien de `gmgn-cli config` + notre `--set-api-key` adapté pour lire `keypair.pem` ; plan Free jusqu'à la fin de la Phase 5 (sous réserve de Q-010) ; installation des skills GMGN pour Claude Code.

**Q-008 · OUVERTE** — Latence réelle : GMGN annonce < 0,3 s pour la soumission ; la confirmation on-chain est distincte et doit être mesurée en Phase 6.
