#!/usr/bin/env bash
# Préparation UNIQUE d'un VPS Ubuntu 24.04 neuf pour MemePilot (mode papier). À lancer en root :
#   ADMIN_PUBKEY="ssh-ed25519 AAAA... toi@pc" DEPLOY_PUBKEY="ssh-ed25519 AAAA... github-deploy" \
#     bash /tmp/memepilot/deploy/bootstrap.sh
# Puis : remplir /opt/memepilot-auto/shared/.env (Telegram) et faire un premier push sur main (ou voir docs/CI_CD.md).
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "à lancer en root" >&2; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
BASE=/opt/memepilot-auto
log() { echo "[bootstrap] $*"; }

log "paquets système"
apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get -yq upgrade
DEBIAN_FRONTEND=noninteractive apt-get -yq install python3-venv python3-pip git curl ufw fail2ban unattended-upgrades
timedatectl set-timezone UTC

log "Node 22 + gmgn-cli 1.7.1 (version épinglée)"
if ! command -v node >/dev/null || [[ "$(node -v)" != v22* ]]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get -yq install nodejs
fi
npm install -g gmgn-cli@1.7.1

log "swap 1 Go"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

log "utilisateurs"
id memepilot >/dev/null 2>&1 || adduser --disabled-password --gecos "" memepilot
if [[ -n "${ADMIN_PUBKEY:-}" ]]; then
  id admin >/dev/null 2>&1 || { adduser --disabled-password --gecos "" admin; usermod -aG sudo admin; }
  install -d -m 700 -o admin -g admin /home/admin/.ssh
  echo "$ADMIN_PUBKEY" > /home/admin/.ssh/authorized_keys
  chown admin:admin /home/admin/.ssh/authorized_keys && chmod 600 /home/admin/.ssh/authorized_keys
  echo "admin ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-admin && chmod 440 /etc/sudoers.d/90-admin
  sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/; s/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
  systemctl restart ssh
  log "SSH : connexion par mot de passe et root désactivées (utilise : ssh admin@IP)"
else
  log "⚠ ADMIN_PUBKEY absent : durcissement SSH NON appliqué (pour ne pas te bloquer dehors)"
fi

log "pare-feu : aucun port entrant sauf SSH"
ufw default deny incoming && ufw default allow outgoing && ufw allow OpenSSH && ufw --force enable

log "arborescence $BASE"
install -d -o root -g root -m 755 "$BASE" "$BASE/bin"
install -d -o memepilot -g memepilot -m 750 "$BASE/releases" "$BASE/shared" "$BASE/shared/data"
install -d -o memepilot -g memepilot -m 700 "$BASE/incoming"
[[ -f "$BASE/shared/.env" ]] || install -o memepilot -g memepilot -m 600 "$SRC/.env.example" "$BASE/shared/.env"
install -o root -g root -m 755 "$SRC/deploy/remote_deploy.sh" "$SRC/deploy/deploy_gate.sh" "$BASE/bin/"

log "droits limités du service : redémarrer SON service, rien d'autre"
echo "memepilot ALL=(root) NOPASSWD: /usr/bin/systemctl restart memepilot-paper" > /etc/sudoers.d/91-memepilot
chmod 440 /etc/sudoers.d/91-memepilot && visudo -cf /etc/sudoers.d/91-memepilot

if [[ -n "${DEPLOY_PUBKEY:-}" ]]; then
  install -d -m 700 -o memepilot -g memepilot /home/memepilot/.ssh
  echo "command=\"$BASE/bin/deploy_gate.sh\",restrict $DEPLOY_PUBKEY" > /home/memepilot/.ssh/authorized_keys
  chown memepilot:memepilot /home/memepilot/.ssh/authorized_keys && chmod 600 /home/memepilot/.ssh/authorized_keys
  log "clé GitHub installée (commande forcée : deploy <sha> uniquement)"
fi

log "services systemd"
install -m 644 "$SRC"/deploy/memepilot-paper.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable memepilot-paper.service   # le rapport hebdomadaire est envoyé par le bot lui-même

log "première version (depuis la copie locale)"
SHA="$(printf '%040d' 0)"
tar -czf "$BASE/incoming/$SHA.tar.gz" -C "$SRC" --exclude=.venv --exclude=data --exclude=.env --exclude=.git .
chown memepilot:memepilot "$BASE/incoming/$SHA.tar.gz"
sudo -u memepilot -H bash "$BASE/bin/remote_deploy.sh" "$SHA"
log "terminé. Interface : ssh -L 8765:127.0.0.1:8765 admin@IP puis http://127.0.0.1:8765"
