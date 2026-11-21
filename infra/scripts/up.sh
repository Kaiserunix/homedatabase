#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${HOMEDATABASE_ENV_FILE:-infra/.env}"
WITH_AI=false
USE_TLS=false
SEED_DEMO=false
BOOTSTRAP_FAMILY=false
BUILD_IMAGES=true
ACTION="up"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-ai|--ai-ocr)
      WITH_AI=true
      shift
      ;;
    --tls)
      USE_TLS=true
      shift
      ;;
    --demo)
      SEED_DEMO=true
      shift
      ;;
    --bootstrap)
      BOOTSTRAP_FAMILY=true
      shift
      ;;
    --no-build)
      BUILD_IMAGES=false
      shift
      ;;
    --down)
      ACTION="down"
      shift
      ;;
    --status)
      ACTION="status"
      shift
      ;;
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./infra/scripts/up.sh [options]

One-command self-hosted Docker Compose launcher for HomeDatabase.
Default runs Core Ledger only (postgres, redis, migrate, api, web on 127.0.0.1:8080).

Options:
  --with-ai, --ai-ocr  Enable optional AI bill-OCR services (ocr-dispatcher, ocr-runner)
  --tls                Enable single-host HTTPS overlay (infra/compose.tls.yml)
  --demo               Seed fictional demo accounts on first start (non-production only)
  --bootstrap          Interactively create family member passwords after startup
  --no-build           Skip --build on docker compose up
  --down               Stop the running Compose stack
  --status             Show Compose service status
  --env-file PATH      Custom env file path (default: infra/.env)
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

PYTHON_BIN="python3"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

COMPOSE_ARGS=(--env-file "$ENV_FILE" -f infra/compose.yml)
if [[ "$USE_TLS" == true ]]; then
  COMPOSE_ARGS+=(-f infra/compose.tls.yml)
fi
if [[ "$WITH_AI" == true ]]; then
  COMPOSE_ARGS+=(--profile ai-ocr)
fi

case "$ACTION" in
  down)
    docker compose "${COMPOSE_ARGS[@]}" down
    exit 0
    ;;
  status)
    docker compose "${COMPOSE_ARGS[@]}" ps
    exit 0
    ;;
esac

if [[ ! -f "$ENV_FILE" ]]; then
  if [[ "$USE_TLS" == true ]]; then
    echo "Missing $ENV_FILE for --tls mode. Generate it first:" >&2
    echo "  $PYTHON_BIN infra/scripts/init_env.py --mode production --host finance.example.com --output $ENV_FILE" >&2
    exit 1
  fi
  "$PYTHON_BIN" infra/scripts/init_env.py --mode local --output "$ENV_FILE"
fi

if [[ "$USE_TLS" == true ]]; then
  "$PYTHON_BIN" infra/scripts/init_env.py --mode production --check "$ENV_FILE"
else
  "$PYTHON_BIN" infra/scripts/init_env.py --check "$ENV_FILE"
fi

UP_ARGS=(up -d --wait)
if [[ "$BUILD_IMAGES" == true ]]; then
  UP_ARGS+=(--build)
fi

docker compose "${COMPOSE_ARGS[@]}" "${UP_ARGS[@]}"

if [[ "$SEED_DEMO" == true ]]; then
  docker compose "${COMPOSE_ARGS[@]}" run --rm api homedatabase-seed --demo || true
elif [[ "$BOOTSTRAP_FAMILY" == true ]]; then
  docker compose "${COMPOSE_ARGS[@]}" run --rm api homedatabase-seed --bootstrap-family
fi

if [[ "$WITH_AI" == true ]]; then
  if grep -q '^HOMEDATABASE_OCR_ENABLED=false' "$ENV_FILE"; then
    echo ""
    echo "AI OCR containers are running (--profile ai-ocr), with HOMEDATABASE_OCR_ENABLED=false."
    echo "Authenticate Codex once before enabling:"
    echo "  docker compose --env-file $ENV_FILE -f infra/compose.yml --profile ai-ocr run --rm ocr-runner codex login --device-auth"
    echo "Then set HOMEDATABASE_OCR_ENABLED=true in $ENV_FILE and re-run this script."
  fi
fi

HTTP_BIND="$(grep -E '^HOMEDATABASE_HTTP_BIND=' "$ENV_FILE" | head -n1 | cut -d= -f2- || echo '127.0.0.1')"
HTTP_PORT="$(grep -E '^HOMEDATABASE_HTTP_PORT=' "$ENV_FILE" | head -n1 | cut -d= -f2- || echo '8080')"
DISPLAY_HOST="$HTTP_BIND"
if [[ "$DISPLAY_HOST" == "0.0.0.0" || -z "$DISPLAY_HOST" ]]; then
  DISPLAY_HOST="localhost"
fi

echo ""
echo "HomeDatabase Core Ledger is healthy:"
echo "  Mobile PWA:    http://${DISPLAY_HOST}:${HTTP_PORT}/"
echo "  Desktop Admin: http://${DISPLAY_HOST}:${HTTP_PORT}/admin.html"
echo "  API Health:    http://${DISPLAY_HOST}:${HTTP_PORT}/api/health"
if [[ "$SEED_DEMO" != true && "$BOOTSTRAP_FAMILY" != true ]]; then
  echo ""
  echo "First-time setup (if no users exist yet):"
  echo "  docker compose --env-file $ENV_FILE -f infra/compose.yml run --rm api homedatabase-seed --bootstrap-family"
fi
