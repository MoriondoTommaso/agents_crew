#!/usr/bin/env bash
# =============================================================================
# install.sh — Self-contained bootstrap for Hybrid Coding Agency + OpenClaw
#
# Supports: macOS (Apple Silicon & Intel) and Linux (Debian/Ubuntu/Arch)
#
# What this script does:
#   1. Checks / installs system deps (git, curl, docker, node, uv, ollama)
#   2. Copies .env.example → .env  (skipped if .env already exists)
#   3. Pulls required Ollama models
#   4. Installs OpenClaw CLI globally via npm
#   5. Configures OpenClaw to point at the local coding-agency server
#   6. Builds the Docker images
#   7. Prints the "ready to go" summary
#
# Usage:
#   chmod +x install.sh
#   ./install.sh
#
# To skip interactive prompts (CI / headless):
#   NON_INTERACTIVE=1 ./install.sh
# =============================================================================
set -euo pipefail

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${GREEN}✔${RESET}  $*"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $*"; }
info() { echo -e "${CYAN}→${RESET}  $*"; }
err()  { echo -e "${RED}✘${RESET}  $*" >&2; exit 1; }

# ── helpers ───────────────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"
NON_INTERACTIVE="${NON_INTERACTIVE:-0}"

ask() {
  # ask <question> — returns 0 (yes) or 1 (no). Auto-yes in non-interactive mode.
  if [[ "$NON_INTERACTIVE" == "1" ]]; then return 0; fi
  read -r -p "$1 [Y/n] " ans
  [[ "${ans,,}" != "n" ]]
}

require_cmd() {
  command -v "$1" &>/dev/null
}

# ── 0. Repo sanity check ──────────────────────────────────────────────────────
if [[ ! -f "pyproject.toml" || ! -f "crew.py" ]]; then
  err "Run this script from the root of the agents_crew repository."
fi

echo
echo -e "${BOLD}╔══════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   Hybrid Coding Agency — Bootstrap Installer  ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════╝${RESET}"
echo -e "  OS: ${OS} / ${ARCH}"
echo

# ── 1. System dependencies ───────────────────────────────────────────────────
info "Checking system dependencies..."

# --- git ---
if ! require_cmd git; then
  warn "git not found."
  if [[ "$OS" == "Darwin" ]]; then
    xcode-select --install 2>/dev/null || true
  else
    sudo apt-get install -y git 2>/dev/null || sudo pacman -S --noconfirm git 2>/dev/null || err "Install git manually."
  fi
fi
log "git: $(git --version)"

# --- docker ---
if ! require_cmd docker; then
  warn "Docker not found."
  if [[ "$OS" == "Darwin" ]]; then
    if require_cmd brew; then
      info "Installing Docker Desktop via Homebrew..."
      brew install --cask docker
    else
      err "Install Docker Desktop from https://www.docker.com/products/docker-desktop"
    fi
  else
    info "Installing Docker Engine..."
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER" || true
    warn "You may need to log out and back in for Docker permissions to take effect."
  fi
fi

# Check daemon is running
if ! docker info &>/dev/null; then
  if [[ "$OS" == "Darwin" ]]; then
    info "Starting Docker Desktop..."
    open -a Docker || true
    info "Waiting for Docker daemon (up to 60s)..."
    for i in $(seq 1 60); do
      docker info &>/dev/null && break
      sleep 1
    done
  fi
  docker info &>/dev/null || err "Docker daemon is not running. Start Docker and re-run this script."
fi
log "docker: $(docker --version)"

# --- Node.js (required for OpenClaw) ---
NODE_MIN=22
if require_cmd node; then
  NODE_VER=$(node --version | sed 's/v//' | cut -d. -f1)
  if [[ "$NODE_VER" -lt "$NODE_MIN" ]]; then
    warn "Node.js $NODE_VER found but OpenClaw requires v$NODE_MIN+."
    require_cmd brew && brew install node@22 && brew link --overwrite node@22 || \
      err "Upgrade Node.js to v$NODE_MIN+ and re-run."
  fi
else
  warn "Node.js not found. Installing..."
  if require_cmd brew; then
    brew install node
  elif [[ "$OS" == "Linux" ]]; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
    sudo apt-get install -y nodejs
  else
    err "Install Node.js v22+ from https://nodejs.org and re-run."
  fi
fi
log "node: $(node --version)"

# --- uv (Python package manager) ---
if ! require_cmd uv; then
  info "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.cargo/bin:$PATH"
fi
log "uv: $(uv --version)"

# --- Ollama (optional — only needed for hybrid/local modes) ---
if ! require_cmd ollama; then
  warn "Ollama not found (needed for PIPELINE_MODE=hybrid or local)."
  if ask "Install Ollama now?"; then
    if [[ "$OS" == "Darwin" ]]; then
      require_cmd brew && brew install ollama || \
        curl -fsSL https://ollama.com/install.sh | sh
    else
      curl -fsSL https://ollama.com/install.sh | sh
    fi
  else
    warn "Skipping Ollama install. Set PIPELINE_MODE=api in .env to run without it."
  fi
fi

if require_cmd ollama; then
  log "ollama: $(ollama --version 2>/dev/null || echo 'installed')"
fi

# ── 2. .env setup ─────────────────────────────────────────────────────────────
info "Setting up environment file..."

if [[ -f ".env" ]]; then
  warn ".env already exists — skipping copy. Edit it manually if needed."
else
  cp .env.example .env
  log ".env created from .env.example"

  if [[ "$NON_INTERACTIVE" != "1" ]]; then
    echo
    warn "Review and fill in your .env settings:"
    echo -e "  ${CYAN}PIPELINE_MODE${RESET}   hybrid | api | local  (default: hybrid)"
    echo -e "  ${CYAN}FREELLM_BASE_URL${RESET} your OpenAI-compatible API endpoint"
    echo -e "  ${CYAN}FREELLMAPI_KEY${RESET}   your API key (or 'none')"
    echo
    if ask "Open .env in your editor now?"; then
      "${EDITOR:-nano}" .env
    fi
  fi
fi

# ── 3. Pull Ollama models (if Ollama is available) ────────────────────────────
if require_cmd ollama; then
  # Ensure daemon is running
  if ! pgrep -x ollama &>/dev/null; then
    info "Starting Ollama daemon..."
    ollama serve &>/dev/null &
    sleep 3
  fi

  info "Pulling required Ollama models (this may take a few minutes)..."

  pull_model() {
    local model="$1"
    if ollama list 2>/dev/null | grep -q "^${model}"; then
      log "${model} already present — skipping pull."
    else
      info "Pulling ${model}..."
      ollama pull "$model"
      log "${model} pulled."
    fi
  }

  # Read PIPELINE_MODE from .env to decide which models are needed
  PIPELINE_MODE_VAL=$(grep -E '^PIPELINE_MODE' .env 2>/dev/null | cut -d= -f2 | tr -d ' ' || echo 'hybrid')

  case "$PIPELINE_MODE_VAL" in
    local)
      pull_model "qwen2.5-coder:12b"
      ;;
    hybrid|*)
      pull_model "qwen2.5-coder:12b"   # coding tasks
      pull_model "qwen2.5:1.5b"        # SML router
      ;;
  esac
fi

# ── 4. Install OpenClaw CLI ───────────────────────────────────────────────────
info "Installing OpenClaw CLI..."

if require_cmd openclaw; then
  CURRENT_OC=$(openclaw --version 2>/dev/null || echo 'unknown')
  log "OpenClaw already installed: ${CURRENT_OC}"
else
  npm install -g openclaw@latest
  log "OpenClaw installed: $(openclaw --version 2>/dev/null)"
fi

# ── 5. Configure OpenClaw to point at the local server ───────────────────────
info "Configuring OpenClaw provider..."

OC_CONFIG_DIR="${HOME}/.openclaw"
mkdir -p "$OC_CONFIG_DIR"

# Copy our pre-built config if openclaw.json doesn't exist yet
if [[ ! -f "${OC_CONFIG_DIR}/openclaw.json" ]]; then
  cp openclaw/openclaw.json "${OC_CONFIG_DIR}/openclaw.json"
  log "OpenClaw config installed at ${OC_CONFIG_DIR}/openclaw.json"
else
  warn "${OC_CONFIG_DIR}/openclaw.json already exists — not overwriting."
  warn "To reset: cp openclaw/openclaw.json ${OC_CONFIG_DIR}/openclaw.json"
fi

# Copy system prompt
if [[ ! -f "${OC_CONFIG_DIR}/system-prompt.md" ]]; then
  cp openclaw/system-prompt.md "${OC_CONFIG_DIR}/system-prompt.md"
  log "System prompt installed."
fi

# ── 6. Build Docker images ────────────────────────────────────────────────────
info "Building Docker images (coding-agency)..."
docker compose build
log "Docker images built."

# ── 7. Summary ────────────────────────────────────────────────────────────────
echo
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║           Installation complete! 🦞           ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════╝${RESET}"
echo
echo -e "  Next steps:"
echo
echo -e "  ${CYAN}1. Start the full stack:${RESET}"
echo -e "     ${BOLD}make up${RESET}"
echo
echo -e "  ${CYAN}2. Attach to the OpenClaw agent (via Docker):${RESET}"
echo -e "     ${BOLD}make agent${RESET}"
echo
echo -e "  ${CYAN}   OR — run OpenClaw locally (it will auto-connect to the server):${RESET}"
echo -e "     ${BOLD}make up-server && openclaw${RESET}"
echo
echo -e "  ${CYAN}3. Change pipeline mode anytime in .env:${RESET}"
echo -e "     ${BOLD}PIPELINE_MODE=hybrid${RESET}  (default)"
echo -e "     ${BOLD}PIPELINE_MODE=api${RESET}     (no Ollama needed)"
echo -e "     ${BOLD}PIPELINE_MODE=local${RESET}   (zero API spend)"
echo
echo -e "  ${CYAN}Full docs:${RESET} README.md"
echo
