#!/usr/bin/env bash
# HookBus Light one-shot installer.
# Usage:
#   curl -fsSL https://agenticthinking.uk/install.sh | bash
#   curl -fsSL https://agenticthinking.uk/install.sh | bash -s -- --runtime hermes
#   curl -fsSL https://agenticthinking.uk/install.sh | bash -s -- --runtime openclaw
#
# Clones the bus, pulls the two free subscribers (AgentProtect + AgentSpend)
# as public Docker images, bootstraps a bearer token, starts the stack, and
# optionally installs the publisher plugin for your chosen agent runtime.
#
# Idempotent. Safe to re-run. Apache 2.0 + MIT throughout.

set -euo pipefail

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
HOOKBUS_DIR="${HOOKBUS_DIR:-$HOME/.hookbus}"
HOOKBUS_REPO="${HOOKBUS_REPO:-https://github.com/agentic-thinking/hookbus.git}"
RUNTIME="${RUNTIME:-}"
NONINTERACTIVE="${NONINTERACTIVE:-0}"

# Parse args (supported after `--` when piped from curl | bash)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime)        RUNTIME="${2:-}"; shift 2 ;;
    --runtime=*)      RUNTIME="${1#*=}"; shift ;;
    --noninteractive) NONINTERACTIVE=1; shift ;;
    --dir)            HOOKBUS_DIR="${2:-}"; shift 2 ;;
    --dir=*)          HOOKBUS_DIR="${1#*=}"; shift ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) shift ;;
  esac
done

# ----------------------------------------------------------------------------
# Output helpers
# ----------------------------------------------------------------------------
if [[ -t 1 ]]; then
  C_G=$'\033[32m'; C_B=$'\033[34m'; C_Y=$'\033[33m'
  C_R=$'\033[31m'; C_BOLD=$'\033[1m'; C_RESET=$'\033[0m'
else
  C_G=""; C_B=""; C_Y=""; C_R=""; C_BOLD=""; C_RESET=""
fi

say()  { printf "%s> %s%s\n" "$C_B" "$*" "$C_RESET"; }
ok()   { printf "%s* %s%s\n" "$C_G" "$*" "$C_RESET"; }
warn() { printf "%s! %s%s\n" "$C_Y" "$*" "$C_RESET"; }
die()  { printf "%sx %s%s\n" "$C_R" "$*" "$C_RESET" >&2; exit 1; }

# ----------------------------------------------------------------------------
# Banner
# ----------------------------------------------------------------------------
cat <<BANNER

${C_BOLD}HookBus Light installer${C_RESET}
Open-source event bus for AI agent lifecycle governance + cost tracking.
Apache 2.0 bus. MIT subscribers. Docker-based, 15 seconds to first event.
Docs: https://github.com/agentic-thinking/hookbus

BANNER

# ----------------------------------------------------------------------------
# Pre-flight
# ----------------------------------------------------------------------------
say "Checking prerequisites..."

command -v docker >/dev/null || \
  die "Docker not found. Install from https://docs.docker.com/engine/install/ and re-run."

docker info >/dev/null 2>&1 || \
  die "Docker daemon not running. Start Docker Desktop or run 'sudo systemctl start docker'."

docker compose version >/dev/null 2>&1 || \
  die "'docker compose' plugin missing. See https://docs.docker.com/compose/install/"

command -v git >/dev/null || \
  die "git not found. Install git and re-run."

command -v openssl >/dev/null || \
  die "openssl not found (needed for token generation)."

ok "Docker + compose + git OK"

# ----------------------------------------------------------------------------
# Clone or update the bus repo
# ----------------------------------------------------------------------------
if [[ -d "$HOOKBUS_DIR/.git" ]]; then
  say "Updating existing install at $HOOKBUS_DIR"
  (cd "$HOOKBUS_DIR" && git pull --quiet --rebase origin main) || \
    warn "git pull failed; keeping existing tree"
else
  say "Cloning bus to $HOOKBUS_DIR"
  git clone --quiet "$HOOKBUS_REPO" "$HOOKBUS_DIR"
fi
ok "Bus repo ready: $HOOKBUS_DIR"

cd "$HOOKBUS_DIR"

# ----------------------------------------------------------------------------
# Generate or reuse bearer token
# ----------------------------------------------------------------------------
ENV_FILE="$HOOKBUS_DIR/.env"
if [[ -f "$ENV_FILE" ]] && grep -q '^HOOKBUS_TOKEN=' "$ENV_FILE"; then
  ok "Reusing existing bearer token from $ENV_FILE"
else
  TOKEN=$(openssl rand -base64 32 | tr -d '/+=')
  {
    echo "# HookBus env, generated $(date -Iseconds)"
    echo "HOOKBUS_TOKEN=$TOKEN"
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  ok "Generated new bearer token in $ENV_FILE"
fi

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

# ----------------------------------------------------------------------------
# Start the stack
# ----------------------------------------------------------------------------
say "Starting bus + AgentProtect + AgentSpend (docker compose up -d)..."
docker compose up -d 2>&1 | tail -10 || die "docker compose failed"

sleep 3
if curl -sf -o /dev/null http://localhost:18800/ 2>/dev/null || \
   curl -sf -o /dev/null -w "%{http_code}" http://localhost:18800/ 2>/dev/null | grep -qE '^(200|401)$'; then
  ok "Bus responding on http://localhost:18800"
else
  warn "Bus not yet responding, may take a few more seconds on first boot."
fi

DASH_URL="http://localhost:18800/?token=${HOOKBUS_TOKEN}"

# ----------------------------------------------------------------------------
# Publisher selection
# ----------------------------------------------------------------------------
if [[ -z "$RUNTIME" && "$NONINTERACTIVE" = "0" ]]; then
  if [[ -t 0 ]]; then
    cat <<MENU

${C_BOLD}Which agent runtime do you want to wire into HookBus?${C_RESET}
  1) Hermes   (Nous Research, Python plugin)
  2) OpenClaw (Node plugin)
  3) Skip     (wire publishers manually later)

MENU
    read -r -p "Choice [1-3]: " choice
    case "$choice" in
      1) RUNTIME="hermes" ;;
      2) RUNTIME="openclaw" ;;
      3|"") RUNTIME="skip" ;;
      *) warn "Unknown choice '$choice', skipping publisher step"; RUNTIME="skip" ;;
    esac
  else
    warn "No TTY for interactive prompt. Re-run with --runtime hermes|openclaw|skip. Skipping for now."
    RUNTIME="skip"
  fi
fi

# ----------------------------------------------------------------------------
# Install chosen publisher
# ----------------------------------------------------------------------------
install_hermes() {
  say "Installing Hermes publisher..."
  local PIP
  PIP=$(command -v pip3 || command -v pip || true)
  if [[ -z "$PIP" ]]; then
    warn "Python pip not found. Install python3-pip and re-run with --runtime hermes."
    return 1
  fi
  $PIP install --quiet --upgrade "git+https://github.com/agentic-thinking/hookbus-publisher-hermes.git" || {
    warn "pip install failed. You can try: $PIP install --user git+https://github.com/agentic-thinking/hookbus-publisher-hermes.git"
    return 1
  }
  ok "Hermes publisher installed."
  cat <<HINT
  Next: pin these env vars before starting hermes-agent
    export HOOKBUS_URL=http://localhost:18800/event
    export HOOKBUS_TOKEN=$HOOKBUS_TOKEN
    export HOOKBUS_SOURCE=hermes-agent
HINT
}

install_openclaw() {
  say "Installing OpenClaw publisher..."
  if ! command -v npm >/dev/null; then
    warn "npm not found. Install Node.js 18+ and re-run with --runtime openclaw."
    return 1
  fi

  local OC_EXT="$HOME/.openclaw/extensions"
  local TMP_DIR
  TMP_DIR=$(mktemp -d)

  git clone --quiet https://github.com/agentic-thinking/hookbus-publisher-openclaw.git "$TMP_DIR/src" || {
    warn "clone failed"; rm -rf "$TMP_DIR"; return 1
  }

  # Plugin id must match target dir name. Read from plugin manifest.
  local PID
  PID=$(python3 -c "import json; print(json.load(open('$TMP_DIR/src/openclaw.plugin.json'))['id'])" 2>/dev/null || echo cre)
  mkdir -p "$OC_EXT/$PID"
  cp -r "$TMP_DIR/src/"* "$OC_EXT/$PID/"
  rm -rf "$TMP_DIR"

  (cd "$OC_EXT/$PID" && npm install --omit=dev --silent 2>&1 | tail -3) || warn "npm install had warnings"

  ok "OpenClaw plugin installed at $OC_EXT/$PID"
  cat <<HINT
  Next: pin these env vars into the openclaw-gateway systemd drop-in
  (~/.config/systemd/user/openclaw-gateway.service.d/hookbus.conf):

    [Service]
    Environment="HOOKBUS_URL=http://localhost:18800/event"
    Environment="HOOKBUS_TOKEN=$HOOKBUS_TOKEN"
    Environment="HOOKBUS_FAIL_MODE=closed"
    Environment="HOOKBUS_SOURCE=openclaw"

  Then: systemctl --user daemon-reload && systemctl --user restart openclaw-gateway
HINT
}

case "$RUNTIME" in
  hermes)   install_hermes   || warn "Hermes publisher install had issues." ;;
  openclaw) install_openclaw || warn "OpenClaw publisher install had issues." ;;
  skip|"")  warn "Skipped publisher install. See $HOOKBUS_REPO for the full shim table." ;;
  *)        warn "Unsupported runtime '$RUNTIME'. Accepted: hermes, openclaw, skip." ;;
esac

# ----------------------------------------------------------------------------
# Final summary
# ----------------------------------------------------------------------------
cat <<DONE

${C_G}${C_BOLD}HookBus Light is running.${C_RESET}

  ${C_BOLD}Dashboard:${C_RESET}  $DASH_URL
  ${C_BOLD}Token:${C_RESET}      saved in $ENV_FILE (chmod 600)
  ${C_BOLD}Compose:${C_RESET}    cd $HOOKBUS_DIR && docker compose ps
  ${C_BOLD}Stop:${C_RESET}       cd $HOOKBUS_DIR && docker compose down
  ${C_BOLD}Docs:${C_RESET}       https://github.com/agentic-thinking/hookbus

Smoke test a manual event:
  curl -s -H "Authorization: Bearer \$HOOKBUS_TOKEN" \\
       -H "Content-Type: application/json" \\
       -d '{"event_id":"test","event_type":"PreToolUse","timestamp":"'$(date -Iseconds)'","source":"manual","session_id":"smoke","tool_name":"ping","tool_input":{},"metadata":{}}' \\
       http://localhost:18800/event

DONE
