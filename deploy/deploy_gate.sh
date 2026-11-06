#!/usr/bin/env bash
# Commande FORCÉE de la clé SSH de GitHub Actions (authorized_keys : command="...",restrict).
# La clé de déploiement ne peut faire QU'UNE chose : « deploy <sha> » avec le paquet sur l'entrée standard.
# Aucun shell, aucun transfert de port, aucun terminal. Fichier possédé par root (non modifiable par la CI).
set -euo pipefail
read -r verb sha extra <<<"${SSH_ORIGINAL_COMMAND:-}"
if [[ "${verb:-}" != "deploy" || ! "${sha:-}" =~ ^[0-9a-f]{40}$ || -n "${extra:-}" ]]; then
  echo "commande refusée" >&2
  exit 2
fi
umask 077
BASE="${MEMEPILOT_BASE:-/opt/memepilot-auto}"
head -c 50000000 > "$BASE/incoming/${sha}.tar.gz"   # 50 Mo max
exec bash "$BASE/bin/remote_deploy.sh" "$sha"
