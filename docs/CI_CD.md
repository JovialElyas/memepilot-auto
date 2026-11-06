# CI/CD — amélioration continue sans risque

```
 ton PC ──git push──▶ GitHub (dépôt PRIVÉ)
                        │
                        ├─ CI (chaque push / pull request)
                        │    ruff · mypy · pytest (Python 3.11/3.12/3.13)
                        │    essai du bot papier hors ligne (2 cycles)
                        │    gitleaks (aucun secret dans l'historique)
                        │
                        └─ CD (branche main seulement, si tout est vert
                           ET variable DEPLOY_ENABLED = true
                           ET ton approbation si tu l'exiges dans l'environnement « production »)
                               │  ssh memepilot@VPS "deploy <sha>" < paquet
                               ▼
                        VPS : deploy_gate.sh (seule commande autorisée pour cette clé)
                               → remote_deploy.sh :
                                 1. releases/<sha>/ + environnement Python
                                 2. tests SUR le serveur          ✘ → l'ancienne version reste active
                                 3. bascule atomique de current/
                                 4. redémarrage du service
                                 5. santé : la NOUVELLE version répond et a fini un cycle
                                                                  ✘ → retour automatique à l'ancienne
```

Vérifié en simulation (2026-11-01) : déploiement normal ✔, commande SSH arbitraire refusée ✔, version avec un test cassé bloquée ✔, version qui plante au démarrage annulée automatiquement ✔.

## Arborescence sur le VPS

```
/opt/memepilot-auto/
  bin/            deploy_gate.sh, remote_deploy.sh   (root : la CI ne peut pas les modifier)
  releases/<sha>/ une version par dossier (5 conservées)
  current ──▶     version active (lien symbolique)
  shared/data/    paper.db, rapports  ← survit aux déploiements
  shared/.env     Telegram (chmod 600) ← jamais dans Git
  shared/service.env  options du bot, ex. MEMEPILOT_ARGS=--plan free --interval 15
```

## Mise en place (une seule fois, ~30 min)

### 1. Dépôt GitHub privé
1. Sur GitHub : **New repository** → nom `memepilot-auto` → **Private** → sans README.
2. Sur ton PC (PowerShell, dans le dossier du projet) :
   ```powershell
   git init -b main
   mkdir .github\workflows -Force
   Copy-Item ci\github-ci.yml .github\workflows\ci.yml
   Copy-Item ci\dependabot.yml .github\dependabot.yml
   git add . ; git status        # vérifie : AUCUN .env, AUCUN data\, AUCUN .db
   git commit -m "MemePilot Auto V2"
   git remote add origin https://github.com/JovialElyas/memepilot-auto.git
   git push -u origin main
   ```
   La CI démarre toute seule (onglet **Actions**). Le déploiement ne se fait pas tant que `DEPLOY_ENABLED` n'existe pas.

### 2. Serveur (après achat du VPS, Ubuntu 24.04)
1. Sur ton PC, deux clés SSH (ne les partage avec personne) :
   ```powershell
   ssh-keygen -t ed25519 -f $HOME\.ssh\memepilot_admin -C "admin"
   ssh-keygen -t ed25519 -f $HOME\.ssh\memepilot_deploy -C "github-deploy" -N '""'
   ```
2. Copier le projet et lancer la préparation (en root, une seule fois) :
   ```powershell
   scp -r src scripts config deploy tests pyproject.toml .env.example root@IP_DU_VPS:/tmp/memepilot/
   ssh root@IP_DU_VPS
   ```
   ```bash
   ADMIN_PUBKEY="<contenu de memepilot_admin.pub>" DEPLOY_PUBKEY="<contenu de memepilot_deploy.pub>" \
     bash /tmp/memepilot/deploy/bootstrap.sh
   ```
   À la fin : SSH par mot de passe et root désactivés, pare-feu actif, bot papier démarré.
3. Telegram sur le serveur : `ssh -i ~/.ssh/memepilot_admin admin@IP` puis
   `sudo -u memepilot nano /opt/memepilot-auto/shared/.env` → `TELEGRAM_BOT_TOKEN=…`, `TELEGRAM_CHAT_ID=…`, puis
   `sudo systemctl restart memepilot-paper`.

### 3. Relier GitHub au serveur
Dans le dépôt : **Settings → Environments → New environment** `production` (cocher *Required reviewers* = toi, recommandé), puis dans **Settings → Secrets and variables → Actions** :

| Type | Nom | Valeur |
|---|---|---|
| Secret | `VPS_HOST` | IP du VPS |
| Secret | `VPS_SSH_KEY` | contenu de `memepilot_deploy` (la clé **privée** de déploiement) |
| Secret | `VPS_KNOWN_HOSTS` | sortie de `ssh-keyscan -t ed25519 IP_DU_VPS` (à vérifier contre l'empreinte affichée par le VPS) |
| Variable | `DEPLOY_ENABLED` | `true` |

Ensuite, chaque `git push` sur `main` est testé puis déployé (après ton clic « Approve » si tu l'as exigé).

## Au quotidien

| Action | Commande |
|---|---|
| Voir l'interface du VPS | `ssh -i ~/.ssh/memepilot_admin -L 8765:127.0.0.1:8765 admin@IP` puis http://127.0.0.1:8765 |
| Journal du bot | `sudo journalctl -u memepilot-paper -f` |
| Arrêt d'urgence (plus d'achat) | `sudo -u memepilot /opt/memepilot-auto/current/.venv/bin/python /opt/memepilot-auto/current/scripts/risk_admin.py halt --reason "..."` |
| Revenir à la version précédente | `sudo -u memepilot ln -sfn /opt/memepilot-auto/releases/<sha> /opt/memepilot-auto/current && sudo systemctl restart memepilot-paper` |
| Changer les options sans redéployer | `shared/service.env` → `MEMEPILOT_ARGS=...` puis `sudo systemctl restart memepilot-paper` |

## Règles qui ne changent pas

- La CI/CD ne déploie que le **mode papier** : le code refuse le live (D-013) et le profil exploration en live (D-021).
- La clé de déploiement n'ouvre aucun shell : elle ne peut qu'envoyer une version. Limite honnête : une version
  déployée exécute son propre code (tests compris) avec les droits de `memepilot`, qui peut lire `shared/.env`.
  La vraie protection est donc en amont : dépôt privé, déploiement depuis `main` seulement, CI verte, et ton
  approbation dans l'environnement `production`. Si la clé de déploiement fuit : supprime sa ligne dans
  `/home/memepilot/.ssh/authorized_keys` et révoque le jeton Telegram.
- Avant le live (Phase 7) : utilisateur de déploiement séparé de celui qui détient des secrets, revue de sécurité indépendante.
