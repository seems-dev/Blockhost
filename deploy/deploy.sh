#!/usr/bin/env bash
# Deploy BlockHost production stack from the deploy/ directory.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [[ ! -f .env ]]; then
  echo "Creating deploy/.env from env.production.example — edit secrets before going live."
  cp env.production.example .env
  echo "Edit deploy/.env then re-run this script."
  exit 1
fi

docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d

echo ""
echo "BlockHost is starting."
echo "  Health:  http://localhost/health  (via Caddy)"
echo "  Legal:   http://localhost/legal/terms/html"
echo "  Logs:    docker compose -f docker-compose.prod.yml logs -f api worker"
echo ""
echo "Register worker nodes: POST /api/nodes/register (admin auth required)"
