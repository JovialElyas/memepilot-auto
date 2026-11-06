#!/usr/bin/env bash
# Déploiement d'une version sur le VPS (exécuté par l'utilisateur `memepilot`).
#   1. décompresse dans releases/<sha>    2. crée son environnement Python    3. lance les tests SUR le serveur
#   4. bascule `current` de façon atomique 5. redémarre le service            6. contrôle de santé
#   → si l'étape 3 ou 6 échoue : l'ancienne version reste/redevient active (retour arrière automatique).
set -euo pipefail
SHA="${1:?usage: remote_deploy.sh <sha>}"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "sha invalide" >&2; exit 2; }
BASE="${MEMEPILOT_BASE:-/opt/memepilot-auto}"
REL="$BASE/releases/$SHA"
PKG="$BASE/incoming/$SHA.tar.gz"
log() { echo "[deploy $(date -u +%H:%M:%S)] $*"; }

[[ -f "$PKG" ]] || { echo "paquet absent : $PKG" >&2; exit 2; }
rm -rf "$REL" && mkdir -p "$REL"
tar -xzf "$PKG" -C "$REL" && rm -f "$PKG"
echo "$SHA" > "$REL/REVISION"   # affichée par l'interface et vérifiée par le contrôle de santé
ln -sfn "$BASE/shared/data" "$REL/data"
ln -sfn "$BASE/shared/.env" "$REL/.env"

log "environnement Python"
python3 -m venv "$REL/.venv"
"$REL/.venv/bin/python" -m pip install -q --upgrade pip
"$REL/.venv/bin/python" -m pip install -q -e "${REL}[dev]"

log "tests sur le serveur"
( cd "$REL" && .venv/bin/python -m pytest -q -p no:cacheprovider )

PREV="$(readlink -f "$BASE/current" 2>/dev/null || true)"
switch_to() { ln -sfn "$1" "$BASE/current.new" && mv -T "$BASE/current.new" "$BASE/current"; }

healthy() {
  for _ in $(seq 1 60); do
    if systemctl is-active --quiet memepilot-paper && \
       curl -fsS --max-time 3 "http://127.0.0.1:${MEMEPILOT_PORT:-8765}/api/state" 2>/dev/null | \
       EXPECT="$SHA" python3 -c 'import json,os,sys; m=json.load(sys.stdin)["meta"]
sys.exit(0 if m.get("version") == os.environ["EXPECT"] and m.get("last_cycle_ts") else 1)' 2>/dev/null; then
      return 0
    fi
    sleep 3
  done
  return 1
}

log "bascule vers $SHA"
switch_to "$REL"
sudo /usr/bin/systemctl restart memepilot-paper
if healthy; then
  log "OK : version $SHA active (elle répond et a terminé un cycle)"
else
  log "ÉCHEC du contrôle de santé → retour à ${PREV:-aucune}"
  if [[ -n "$PREV" && -d "$PREV" ]]; then
    switch_to "$PREV"
    sudo /usr/bin/systemctl restart memepilot-paper
  fi
  exit 1
fi

# Garder les 5 dernières versions (retour arrière manuel possible) — jamais celle qui est active
CUR="$(readlink -f "$BASE/current")"
# shellcheck disable=SC2012  # noms de dossiers = sha hexadécimaux
ls -1dt "$BASE"/releases/*/ | tail -n +6 | while read -r d; do
  [[ "$(readlink -f "$d")" == "$CUR" ]] || rm -rf "$d"
done
