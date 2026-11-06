# MemePilot Auto V2

Système de trading de memecoins Solana via GMGN, encadré par des garde-fous déterministes.
**Aucune promesse de rentabilité.** Perte maximale cumulée : 20 USD (codée en dur).

État : **Phase 5 (papier)** — connecteur GMGN lecture seule, signaux et stratégie hybride, Risk Guardian, **trading papier complet** (ledger SQLite, sorties automatiques, Telegram, rapport hebdomadaire). **Aucun ordre réel possible.**

⚠ Blocages en cours (clé API à 100 $, plan Plus à 29 $/mois) : voir **[`docs/BLOCKERS.md`](docs/BLOCKERS.md)** à chaque reprise.
Documentation : [`docs/`](docs/) — voir notamment `INTEGRATION_PLAN.md`, `GMGN_FIELD_MAP.md`, `DECISIONS.md`.

## Installation (Windows PowerShell)

Prérequis : Python 3.11+, Node.js 20+, gmgn-cli 1.7.1 (déjà installé chez toi : ne pas réinstaller).

```powershell
cd $HOME\Downloads\memepilot-auto
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest           # doit afficher : 264 passed
```

## Phase 1 — Clé API et wallet (à faire par toi)

1. Connectivité : `python scripts\check_wallet_binding.py --demo`
2. Clé de signature + lien pré-rempli : `gmgn-cli config` → ouvre le lien, crée la clé **sans activer le trading**.
   Le message « send me the API Key » s'adresse à un agent IA : **ne l'envoie à personne**.
3. Enregistre la clé API (saisie masquée, rien dans l'historique PowerShell) :
   `python scripts\generate_signing_key.py --set-api-key`
4. Vérifie le wallet : `python scripts\check_wallet_binding.py --expect <adresse_wallet_trading_GMGN>`
5. Envoie-moi seulement la **sortie du script**. Jamais le contenu de `~/.config/gmgn/`.

Si l'étape 4 échoue avec 401/403 : vérifier la règle « solde minimum de 100 $ » du plan Free (Q-010).

## Phase 2 — Scan en lecture seule

```powershell
python scripts\scan_once.py                          # un cycle avec ta clé
python scripts\scan_once.py --demo                   # clé de démo publique (partagée : 429 fréquents)
python scripts\scan_once.py --loop 60 --interval 30  # critère de sortie : 30 min sans 429
python scripts\record_fixtures.py                    # enregistre de nouvelles réponses réelles pour les tests
```

Chaque cycle : prix SOL → `market trending` + `market trenches` (filtres serveur) → validation → préfiltre de sécurité (côté client) → enrichissement des 3 meilleurs (`token security` + `token pool`). Les rapports sont écrits dans `data\scans\`. **Décision : aucune.**

## Phase 3 — Interface de test (papier)

```powershell
python scripts\dashboard.py --offline --cycles 1   # sans réseau (données enregistrées)
python scripts\dashboard.py --demo                 # en direct, clé de démo, un cycle / 60 s (Ctrl+C pour arrêter)
```
La page `data\dashboard.html` s'ouvre et se recharge seule : décisions, scores par module, raisons des NO_TRADE, rejets de sécurité, historique. Les décisions notables sont journalisées dans `data\decisions.jsonl`.

## Phase 4 — Risk Guardian

Chaque PROPOSE de l'interface passe devant le Guardian (colonne « Guardian » : taille autorisée ou motif du refus), avec un panneau budget de risque.

```powershell
python scripts\risk_admin.py status                      # capital, pertes jour/cumul, exposition, budget
python scripts\risk_admin.py halt --reason "test"         # arrêt d'urgence : plus aucun achat
python scripts\risk_admin.py resume --reason "ok"
python scripts\risk_admin.py unlock --note "post-mortem"  # seulement après un LOCK (phrase à taper)
```

## Phase 5 — Trading papier continu

```powershell
python scripts\paper_trade.py --demo            # scan toutes les 30 s, positions surveillées toutes les 2 s
                                                # interface live : http://127.0.0.1:8765 (mise à jour toutes les 2 s)
python scripts\paper_trade.py --demo --profile strict   # mêmes règles que le futur live (très peu de trades)
python scripts\paper_trade.py --offline --cycles 3 --interval 1   # démonstration sans réseau
python scripts\weekly_report.py                 # bilan (7 jours) calculé depuis data\paper.db
python scripts\weekly_report.py --days 14 --send
```
- Chaque cycle : surveillance et sorties (stop-loss −25 %, trailing 20 % après +50 %, time-stop 30 min, chute de liquidité, prix absent) → analyse → Guardian → achat **papier**.
- Arrêt avec Ctrl+C : tout est sauvegardé ; au redémarrage, l'état reprend et est réconcilié avec le ledger (désaccord → HALT).
- `--fresh` archive l'ancien portefeuille papier et repart à 50 $.
- Profil **exploration** (défaut, papier uniquement, D-021) : seuil 55, une confirmation, pondération orientée momentum. Sécurité, vetos, Guardian et refus du risque HIGH inchangés. Chaque trade note si le profil strict l'aurait pris (`strict_ok`) ; le verdict économique du rapport n'utilise que ceux-là.
- Telegram (facultatif, notifications sortantes uniquement) :
  1. Mets `TELEGRAM_BOT_TOKEN=...` dans le `.env` du projet.
  2. Dans Telegram, ouvre ton bot et appuie sur « Démarrer » (ou envoie « bonjour »).
  3. `python scripts\telegram_setup.py` : trouve ton CHAT ID, propose de l'écrire dans `.env`, envoie un message de test.
- Arrêt d'urgence pendant que le bot tourne (dans une 2e fenêtre) : `python scripts\risk_admin.py halt --reason "..."` → plus aucun achat dès le cycle suivant.
- Vitesse : deux boucles. Le **scan** (marché + analyse, ≈ 6 s de travail, appels en parallèle) tourne toutes les `--interval` s ; la **surveillance** des positions ouvertes (stop-loss, trailing…) tourne toutes les `--monitor` s (2 par défaut, D-030). Un achat n'est exécuté qu'après ~5 s si le prix est resté calme. Plancher : 20 s sur la clé de démo partagée, 15 s / 8 s / 5 s avec une clé personnelle Free / Plus / Pro (`--plan`).
- Hébergement 24 h/24 et CI/CD : [`docs/DEPLOYMENT_PLAN.md`](docs/DEPLOYMENT_PLAN.md) puis [`docs/CI_CD.md`](docs/CI_CD.md).

## Sources de données : GMGN + relais gratuit (D-024)

Par défaut (`--source auto`), le bot interroge GMGN ; dès que GMGN bloque (clé de démo saturée), les mêmes requêtes
passent par un **relais gratuit** : DexScreener (prix), GeckoTerminal (tendances, nouveaux tokens), RugCheck (sécurité).
L'interface indique la source de chaque cycle (« GMGN », « relais gratuit », « GMGN + relais »).
- `--source gmgn` : GMGN seul · `--source relay` : relais seul (test sans GMGN).
- En relais, le module smart money est en retrait (ce flux n'existe que chez GMGN).
- Repartir de zéro (nouveau portefeuille papier à 50 $, l'ancien est archivé) : arrêter le bot, puis
  `python scripts\paper_trade.py --reset`, puis relancer `.\scripts\run_forever.ps1`.

## Faire tourner le bot en continu sur ton PC (sans hébergement, D-023)

```powershell
.\scripts\run_forever.ps1                 # lance le bot et le relance tout seul s'il plante
.\scripts\install_autostart.ps1           # facultatif : démarrage automatique à l'ouverture de session
.\scripts\install_autostart.ps1 -Remove   # pour l'enlever
```
- Tant que le bot tourne, Windows ne se met pas en veille tout seul (l'écran peut s'éteindre). `--allow-sleep` pour désactiver.
- **Portable** : fermer le capot met le PC en veille quoi qu'il arrive. Si tu veux pouvoir le fermer branché :
  Panneau de configuration → Options d'alimentation → « Choisir l'action qui suit la fermeture du capot » → *Sur secteur : Ne rien faire*.
- Si le PC s'éteint ou dort : aucune perte, tout reprend au redémarrage (les positions papier sont revérifiées immédiatement).
- **Une seule fenêtre à la fois** : un second bot est refusé (deux bots divisent le débit GMGN et provoquent des bannissements).
- Si PowerShell refuse les scripts : `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (une fois).

## Calibrer les sorties sur les vrais prix (D-026)

```powershell
python scripts\replay_exits.py      # compare ~15 règles de sortie sur les prix enregistrés de tes trades
```
**Laboratoire (D-041)** : `run_forever.ps1 -BotArgs "--plan free --observe"` fait tourner le bot SANS acheter : chaque candidat analysé est suivi 15 min (prix toutes les 5 s par lots DexScreener) avec ses caractéristiques. `python scripts\lab_report.py` teste les hypothèses (dont H1 : momentum ≥ 80) en achat virtuel de 5 $ ; une hypothèse n'est validée que si elle gagne sur les deux moitiés de la période, avec ≥ 30 cas chacune.

**Bilan des refus (D-037)** : `python scripts\review_refusals.py` répond à « et si on avait acheté ? » pour les tokens refusés (pic en cours, achat annulé à la confirmation, refus du Guardian ou du contrôle, juste sous le seuil). Chaque refus est suivi 30 min puis rejoué avec les règles actuelles. Aussi dans le rapport hebdomadaire.

**Chien de garde (D-037)** : message Telegram si le bot était arrêté (au redémarrage), si le PC s'est mis en veille, si aucun cycle complet depuis 5 min, ou si une position n'est plus surveillée depuis 1 min. Pour être prévenu même PC éteint : `MEMEPILOT_HEALTHCHECK_URL` dans `.env` (voir `.env.example`, service gratuit healthchecks.io).

**Objectif de rentabilité (D-031)** : `python scripts\goal_status.py` dit où on en est. Gagné si, sur 30 trades fermés depuis les nouvelles règles, le P&L net (frais compris) est positif **ou** si au moins 75 % des trades sont gagnants. Le rapport hebdomadaire et Telegram affichent la même ligne.

Chaque position enregistre son prix toutes les 2 s (5 s après la vente), et encore 30 min après la vente : on peut donc voir ce qu'aurait donné une règle plus serrée ou plus lâche. À utiliser après ~30 trades.

## Prochaines étapes

Voir le tableau en fin de [`docs/ROADMAP.md`](docs/ROADMAP.md) et la décision D-023 : pas d'hébergement avant que le papier soit rentable sur des trades jamais vus pendant la calibration.

## Développement

```powershell
python -m ruff check .
python -m mypy
python -m pytest
```
