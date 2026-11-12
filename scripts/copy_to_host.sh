#!/usr/bin/env bash
# Copy source only. Host .env, profiles.yaml, secrets, and Docker volumes are preserved.
set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo "Usage: bash scripts/copy_to_host.sh user@host /path/to/ssh-key" >&2
  exit 1
fi
cd "$(dirname "$0")/.."
tar --exclude=__pycache__ --exclude='*.pyc' -czf - \
  .dockerignore .gitignore .env.example Dockerfile README.md \
  app docker-compose.yml profiles.example.yaml requirements.txt scripts tests docs \
  | ssh -i "$2" -o IdentitiesOnly=yes -o BatchMode=yes "$1" \
      'sudo -n mkdir -p /opt/tiktok-vault && sudo -n tar -xzf - -C /opt/tiktok-vault'
