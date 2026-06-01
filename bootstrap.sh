#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# bootstrap.sh — Seed the knowledge graph from ANY project directory.
#
# Usage:
#   ./bootstrap.sh                          # current directory → group = dirname
#   ./bootstrap.sh ~/projects/other-project # explicit path
#   ./bootstrap.sh . my-group-id            # custom namespace
#
# Requirements:
#   - Docker stack running (neo4j + memory containers)
#   - memory-mcp:latest image available (built by `make up` or `docker compose build memory`)
#   - Ollama or cloud embedder reachable (same env as the memory service)
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")" && pwd)"
PROJECT_DIR="${1:-$(pwd)}"
GROUP_ID="${2:-$(basename "$(realpath "$PROJECT_DIR")")}"

# Resolve to absolute path
PROJECT_DIR="$(realpath "$PROJECT_DIR")"

echo "═══ Graphiti Bootstrap ═══"
echo "  Project:  $PROJECT_DIR"
echo "  Group ID: $GROUP_ID"

# ── Load env ──────────────────────────────────────────────────────────────────
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
  echo "  cd \"$SCRIPT_DIR\" && make up"
  exit 1
fi

# ── Check image ───────────────────────────────────────────────────────────────
if ! docker image inspect memory-mcp:latest >/dev/null 2>&1; then
  echo "Building memory-mcp:latest ..."
  docker compose -f "$SCRIPT_DIR/docker-compose.yml" build memory
fi

echo ""
echo "Ingesting files ..."
echo "─────────────────────────────────────────────────────────────────────────"

docker run --rm \
  --network container:memory \
  -v "$PROJECT_DIR:/workspace:ro" \
  -e NEO4J_URI="bolt://neo4j:7687" \
  -e NEO4J_USER="neo4j" \
  -e NEO4J_PASSWORD="${NEO4J_PASSWORD:-changeme}" \
  -e FREELLM_BASE_URL="${FREELLM_BASE_URL:-http://host.docker.internal:3001/v1}" \
  -e FREELLM_API_KEY="${FREELLM_API_KEY:-freellm}" \
  -e OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://host.docker.internal:11434}" \
  -e GRAPHITI_LLM_MODEL="${GRAPHITI_LLM_MODEL:-auto}" \
  -e GRAPHITI_EMBED_PROVIDER="${GRAPHITI_EMBED_PROVIDER:-ollama}" \
  -e GRAPHITI_EMBED_MODEL="${GRAPHITI_EMBED_MODEL:-nomic-embed-text}" \
  -e GRAPHITI_EMBED_DIM="${GRAPHITI_EMBED_DIM:-768}" \
  -e GRAPHITI_EMBED_BASE_URL="${GRAPHITI_EMBED_BASE_URL:-}" \
  -e GRAPHITI_EMBED_API_KEY="${GRAPHITI_EMBED_API_KEY:-}" \
  -e GRAPHITI_GROUP_ID="$GROUP_ID" \
  -e BOOTSTRAP_EPISODE_DELAY="${BOOTSTRAP_EPISODE_DELAY:-0}" \
  memory-mcp:latest \
  python /app/bootstrap.py --dir /workspace --group-id "$GROUP_ID"

echo ""
echo "═══ Done — group '$GROUP_ID' seeded ═══"
