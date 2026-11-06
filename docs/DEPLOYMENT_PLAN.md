# DEPLOYMENT_PLAN — héberger le bot papier sur un VPS
> **Statut (D-023) : reporté volontairement jusqu'à ce que le papier soit rentable.** Le bot tourne sur le PC en attendant.

**Statut : PLAN SEULEMENT. Rien n'est déployé.** Le déploiement attend la porte de qualité (§1) et ton accord explicite.
Objectif : que `paper_trade.py` tourne 24 h/24 sans ton PC (blocage B-09), au coût annuel le plus bas.
Le live (Phase 7) ajoutera ses propres exigences (§7).

## 1. Porte de qualité — « code propre et version papier qui marche bien »

On ne loue ni n'installe rien tant que **toutes** ces cases ne sont pas cochées :

| # | Critère | Comment le vérifier |
|---|---|---|
| G1 | Tests, lint, typage verts sous Windows | `python -m pytest` (196 passed), `python -m ruff check .`, `python -m mypy` |
| G2 | 72 h de papier sur ton PC **sans crash** | aucune ligne « ✘ cycle … en erreur » répétée ; `data\paper.db` → table `events` sans HALT inexpliqué |
| G3 | Le bot **trade** vraiment | ≥ 5 trades papier fermés en 72 h (profil exploration, D-021) |
| G4 | Sorties testées en réel | au moins une sortie stop-loss, time-stop ou trailing dans le ledger |
| G5 | Telegram reçu | message de démarrage et message d'achat reçus sur ton téléphone |
| G6 | Arrêt d'urgence testé | `python scripts\risk_admin.py halt --reason test` → plus d'achat au cycle suivant ; puis `resume` |
| G7 | Redémarrage propre | Ctrl+C puis relance : positions reprises, pas de HALT de réconciliation |

## 2. Besoins réels du bot (mode papier)

- 1 processus Python + des appels courts à `gmgn-cli` (Node) : **≈ 150–300 Mo de RAM**, CPU quasi nul, quelques Mo de disque par semaine.
- Réseau sortant uniquement (GMGN + Telegram). **Aucun port entrant** à part SSH.
- Une **IPv4 publique fixe** : la clé API GMGN personnelle (quand tu l'auras) se lie à une liste blanche d'IP, en IPv4 uniquement.
→ 1 vCPU / 1 Go de RAM suffit largement, avec 1 Go de swap par sécurité.

## 3. Comparatif (prix relevés fin octobre 2026 — à revérifier au moment d'acheter)

| Offre | Prix annuel | Ressources | IPv4 | Points d'attention |
|---|---|---|---|---|
| **Oracle Cloud Always Free** | **0 $** | 2× VM E2.1.Micro (1/8 OCPU, 1 Go) ; ou Ampere A1 jusqu'à 2 OCPU / 12 Go depuis le 15/06/2026 | oui | Carte bancaire demandée à l'inscription ; capacité A1 souvent indisponible ; **récupération des instances jugées inactives** (CPU p95 < 20 %, réseau < 20 % et, pour A1, mémoire < 20 % sur 7 jours) : notre bot consomme très peu → risque réel d'arrêt sauf passage en compte payant (Pay-As-You-Go, qui reste gratuit dans les limites) |
| **RackNerd KVM 1 Go** (promo) | **≈ 22 $/an** (21,99 $) | 1 vCPU, 1 Go, SSD | oui, incluse | Prix promo, renouvellement annoncé au même tarif ; datacenters US (New York, Atlanta, Ashburn…) ; support basique |
| OVHcloud VPS-1 | ≈ 46 €/an (3,81 €/mois en engagement 12 mois) | 2 vCPU, 4 Go | oui | Très fiable, plus cher |
| netcup VPS Lite | ≈ 59 €/an (4,92 €/mois, 6 mois mini) | selon gamme | oui | Allemagne |
| Hetzner CX23 | ≈ 72 €/an (5,99 €/mois) | 2 vCPU, 4 Go | oui | Référence de fiabilité, mais pas le moins cher |

## 4. Recommandation

1. **RackNerd 1 Go à ≈ 22 $/an** : la solution payante la moins chère et la plus prévisible pour un bot qui doit tourner 2 semaines **sans interruption**. Choisir un datacenter côte Est US (Ashburn ou New York).
2. **Oracle Always Free (0 $)** si tu veux strictement zéro dépense : ça marche, mais un bot aussi léger risque d'être classé « inactif » et arrêté, ce qui casserait la période de mesure. Passer le compte en Pay-As-You-Go évite cette récupération (sans frais tant qu'on reste dans le gratuit), mais demande une carte bancaire.

Dans les deux cas : **même procédure d'installation** (§5), et l'IP du VPS devient l'IP de la future clé API GMGN.

## 5. Procédure d'installation — automatisée

Tout ce qui était manuel ici est désormais fait par **`deploy/bootstrap.sh`** (une seule fois, en root), puis
chaque mise à jour passe par la **CI/CD**. Procédure complète, secrets GitHub et commandes du quotidien :
**[`CI_CD.md`](CI_CD.md)**.

Ce que fait `bootstrap.sh` : mises à jour de sécurité automatiques, pare-feu (SSH seulement), fail2ban, swap 1 Go,
Node 22 + gmgn-cli 1.7.1 épinglé, utilisateur `memepilot` sans sudo (sauf redémarrer son propre service),
SSH par clé uniquement, clé GitHub limitée à « deploy <sha> », service systemd durci, premier déploiement testé.

Accès à l'interface depuis ton PC : tunnel SSH vers 127.0.0.1:8765 (aucun port web ouvert sur Internet).

## 6. Ce qui ne change pas sur le VPS

- Mode **papier uniquement** : `MEMEPILOT_MODE=paper`, le code refuse le live (D-013).
- Telegram reste **sortant** : aucune commande ne peut être envoyée au bot depuis Telegram.
- Aucun secret dans Git, dans les journaux ou dans `deploy/`.

## 7. Ajouts obligatoires avant le live (Phase 7, hors périmètre ici)

Revue de sécurité indépendante ; plan GMGN Plus (Q-009) ; clé API liée à l'IP du VPS ; journaux sans secrets vérifiés ; LOCK testé sur le serveur ; supervision (alerte Telegram si aucun cycle depuis 10 min) ; procédure de restauration testée.

## Sources (prix et limites, relevés fin octobre 2026)

- Oracle — ressources Always Free : https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm
- InfoQ — réduction des limites A1 (juillet 2026) : https://www.infoq.com/news/2026/07/oracle-cloud-free-tier-limits/
- Comparatif de 24 VPS (octobre 2026) : https://dev.to/hostingsift/is-hetzner-still-the-cheapest-vps-we-priced-24-alternatives-october-2026-271g
- ServerCalc — VPS bon marché : https://servercalc.com/cheap-vps/
- Avis RackNerd 2026 : https://vpsdex.com/en/activity/racknerd-review-2026/
