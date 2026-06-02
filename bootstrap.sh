#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# bootstrap.sh — Seed the knowledge graph from ANY project directory.
#
# Usage:
#   ./bootstrap.sh                              # pwd → group = dirname
#   ./bootstrap.sh ~/projects/other-project      # explicit path
#   ./bootstrap.sh ~/projects/other-project foo  # custom namespace
#   ./bootstrap.sh --dry-run                     # dry run only
#
# Requirements:
#   - Docker stack running (neo4j + memory containers: `docker compose up -d`)
#   - memory-mcp:latest image (auto-built if missing)
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
TARGET_DIR=""
GROUP_ID=""
DRY_RUN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN="--dry-run"; shift ;;
    --) shift; break ;;
    *) 
      if [ -z "$TARGET_DIR" ]; then
        TARGET_DIR="$1"
      elif [ -z "$GROUP_ID" ]; then
        GROUP_ID="$1"
      fi
      shift ;;
  esac
done

TARGET_DIR="${TARGET_DIR:-$(pwd)}"
TARGET_DIR="$(realpath "$TARGET_DIR")"
GROUP_ID="${GROUP_ID:-$(basename "$TARGET_DIR")}"

echo "═══ Graphiti Bootstrap ═══"
echo "  Project:  $TARGET_DIR"
echo "  Group ID: $GROUP_ID"
[ -n "$DRY_RUN" ] && echo "  Mode:     dry-run"

# ── Load .env from agents_crew directory ─────────────────────────────────────
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
  echo "  Env:      $ENV_FILE"
else
  echo "  Env:      (none — using defaults)"
fi

# ── Check memory container ────────────────────────────────────────────────────
if ! docker container inspect memory >/dev/null 2>&1; then
  echo "ERROR: 'memory' container is not running. Start the stack first:"
  echo "  cd \"$SCRIPT_DIR\" && docker compose up -d"
  exit 1
fi

# ── Check / build image ───────────────────────────────────────────────────────
if ! docker image inspect memory-mcp:latest >/dev/null 2>&1; then
  echo "Building memory-mcp:latest ..."
  docker compose -f "$SCRIPT_DIR/docker-compose.yml" build memory
fi

echo ""
echo "Ingesting files ..."
echo "─────────────────────────────────────────────────────────────────────────"

docker run --rm \
  --network container:memory \
  -v "${TARGET_DIR}:/target:ro" \
  -v "${SCRIPT_DIR}/memory/bootstrap.py:/app/bootstrap.py:ro" \
  -v "${SCRIPT_DIR}/memory/service.py:/app/service.py:ro" \
  -e NEO4J_URI="bolt://neo4j:7687" \
  -e NEO4J_USER="neo4j" \
  -e NEO4J_PASSWORD="${NEO4J_PASSWORD:-changeme}" \
  -e OPENAI_API_KEY="${OPENAI_API_KEY:-ollama}" \
  -e FREELLM_BASE_URL="${FREELLM_BASE_URL:-http://host.docker.internal:3001/v1}" \
  -e FREELLM_API_KEY="${FREELLM_API_KEY:-freellm}" \
  -e GRAPHITI_LLM_MODEL="${GRAPHITI_LLM_MODEL:-auto}" \
  -e OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.docker.internal:11434}" \
  -e GRAPHITI_EMBED_PROVIDER="${GRAPHITI_EMBED_PROVIDER:-ollama}" \
  -e GRAPHITI_EMBED_MODEL="${GRAPHITI_EMBED_MODEL:-nomic-embed-text}" \
  -e GRAPHITI_EMBED_DIM="${GRAPHITI_EMBED_DIM:-768}" \
  -e GRAPHITI_EMBED_BASE_URL="${GRAPHITI_EMBED_BASE_URL:-}" \
  -e GRAPHITI_EMBED_API_KEY="${GRAPHITI_EMBED_API_KEY:-}" \
  -e GRAPHITI_GROUP_ID="${GROUP_ID}" \
  -e BOOTSTRAP_EPISODE_DELAY="${BOOTSTRAP_EPISODE_DELAY:-0}" \
  memory-mcp:latest \
  python /app/bootstrap.py --dir /target --group-id "${GROUP_ID}" ${DRY_RUN}

echo ""
echo "═══ Done — group '${GROUP_ID}' seeded ═══"
