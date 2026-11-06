# BLOCKERS — Points bloquants et reprise de session

> Dernière mise à jour : 2026-10-31. À lire en premier à la reprise du projet.
> Décision en vigueur (D-017) : **GMGN uniquement, clé de démo, zéro dépense** jusqu'à ce que les fonds soient disponibles.

## 1. Où en est le projet

| Phase | État | Détail |
|---|---|---|
| 0 — Audit et architecture | ✅ | 7 documents + audits (démo, skills, CLI, Phantom) |
| 1 — Structure + vérification wallet | 🟡 code ✅, **vérification bloquée** | Bloqué par B-01 |
| 2 — Connecteur lecture seule | 🟡 code ✅, **validation 30 min bloquée** | Bloqué par B-01 ; validé sur la clé de démo |
| 3 — Signaux + stratégie hybride | ✅ code (papier) | 3 modules, agrégation, NO_TRADE, interface de test |
| 4 — Risk Guardian | ✅ code (papier) | Guardian pur, LOCK/HALT persistants, tests de propriétés |
| 5 — Paper trading + ledger + Telegram | 🟡 code ✅, **2 semaines de papier à faire tourner** | `scripts\paper_trade.py --demo` ; fragile avec la clé de démo (B-03) |
| 6+ — Exécution réelle | ⛔ | Bloqué par B-01, B-02, B-04 |

Tests : 178 passent, dont des tests de propriétés Hypothesis (ruff + mypy strict propres, couverture 94 %).

## 2. Blocages

| ID | Blocage | Impact | Débloqué quand | Coût |
|---|---|---|---|---|
| **B-01** | GMGN exige **100 $ de solde** sur le compte pour créer une clé API, même Free et en lecture seule (Q-010) | Pas de clé personnelle → pas de vérification du wallet (Phase 1), pas de test de 30 min (Phase 2), dépendance à la clé de démo | Solde GMGN ≥ 100 $ | 100 $ immobilisés |
| **B-02** | `swap` (poids 10) impossible sur le plan Free (capacité 5) → **plan Plus obligatoire** pour trader (Q-009) | Aucune exécution réelle possible | Abonnement Plus actif | **29 $/mois** (offre limitée, 60 $ normalement) ou 5 000 pts/mois |
| **B-03** | Clé de démo **partagée** : bannissements 429 fréquents (constatés le 2026-10-31) | Cycles interrompus ; un paper trading continu de 2 semaines (critère Phase 5) sera irrégulier | B-01 levé | — |
| **B-04** | Garde des fonds du wallet de trading GMGN et procédure de retrait inconnues (Q-002) | Risque de ne pas pouvoir récupérer les fonds | Test de retrait de ~1 $ avant tout dépôt | ~1 $ |
| **B-05** | Rentabilité non démontrée : coûts fixes (29 $/mois) > budget de perte (20 $) | Le projet peut être techniquement prêt mais économiquement perdant | Résultats de la Phase 5 | — |
| B-06 | Honeypot non détecté par GMGN sur Solana ; `order quote` (simulation de vente) indisponible sur Free (Q-005) | Filtre de sécurité incomplet | B-02 levé, ou filtres compensatoires | — |
| ~~B-07~~ | ✅ Résolu le 2026-11-01 : tests et scripts validés sous Windows par le propriétaire | — | — | — |
| B-09 | Le papier doit tourner **2 semaines en continu** : le PC doit rester allumé | Les coupures fragmentent les données (sans risque : l'état reprend au redémarrage) | Reporté (D-023) : le bot tourne sur le PC (`run_forever.ps1`, démarrage auto, veille suspendue). VPS RackNerd ≈ 22 $/an et CI/CD prêts pour quand le papier sera rentable | ⏸ volontairement reporté |
| B-10 | Stratégie stricte très sélective : **0 proposition** sur les premiers cycles réels | Sans trades, le rapport ne peut pas conclure (≥ 50 trades requis) | ✅ Profil **exploration** papier (D-021) + enrichissement des données : 1er achat papier mesuré le 2026-11-01. Reste à vérifier sur plusieurs jours que le rythme atteint ~50 trades en 2 semaines | 🟡 en mesure |
| B-08 | Fichier CI dans `ci/github-ci.yml` (le dossier `.github/` est protégé en écriture distante) ; pas de dépôt Git | Pas d'historique ni de CI | Créer un dépôt **privé** et déplacer le fichier | — |

> **2026-11-01 (D-024)** : la saturation de la clé de démo (B-01 en attendant les 100 $) est contournée par le relais gratuit DexScreener + GeckoTerminal + RugCheck : 10 cycles complets sur 10 mesurés. B-01 reste la vraie solution (clé personnelle).

## 3. Coût total estimé pour passer au trading réel

| Poste | Montant | Nature |
|---|---|---|
| Solde minimum GMGN | 100 $ | Immobilisé (récupérable si B-04 est résolu) |
| Capital de trading | 50 $ (perte max 20 $) | À risque |
| Plan Plus | 29 $/mois | Dépense |
| VPS Linux | 0 $ (Oracle Always Free) à ≈ 22 $/an (RackNerd) — voir `DEPLOYMENT_PLAN.md` | Dépense |

**Règle de décision proposée** : ne payer Plus que si la Phase 5 montre une espérance de gain mensuelle **supérieure à 29 $** après frais, sur ≥ 50 décisions papier.

## 4. Ce qui peut avancer sans fonds (dans l'ordre)

1. B-07 : tests sous Windows (2 commandes).
2. ~~Phase 4~~ ✅ livrée.
3. ~~Phase 5 (code)~~ ✅ livrée → **lancer `python scripts\paper_trade.py --demo` en continu** (2 semaines) et consulter `python scripts\weekly_report.py`.
4. Calibrer les seuils (STRATEGY) sur le journal papier.

## 5. Reprendre la session : checklist

```powershell
cd $HOME\Downloads\memepilot-auto
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest                                   # 178 passed attendu
python scripts\paper_trade.py --demo              # trading papier continu (Ctrl+C pour arrêter, reprise automatique)
python scripts\weekly_report.py                    # bilan calculé depuis le ledger
python scripts\risk_admin.py status               # état du Risk Guardian (papier)
python scripts\dashboard.py --offline --cycles 1   # interface sans réseau
python scripts\dashboard.py --demo                 # interface en direct (clé de démo), Ctrl+C pour arrêter
```
Puis lire, dans cet ordre : ce fichier → `DECISIONS.md` (D-009 à D-018, Q-002 à Q-010) → `INTEGRATION_PLAN.md` → `STRATEGY.md`.

Quand les fonds sont là :
1. Test de retrait de ~1 $ (B-04).
2. Solde GMGN ≥ 100 $ → `gmgn-cli config` → clé **sans trading** → `python scripts\generate_signing_key.py --set-api-key`.
3. `python scripts\check_wallet_binding.py --expect <wallet de trading>` (Phase 1).
4. `python scripts\scan_once.py --loop 60 --interval 30` (Phase 2 : 30 min sans 429).
5. Remplacer `--demo` par la clé personnelle dans l'interface.
