#!/usr/bin/env bash
# HookBus Light one-shot installer.
# Usage:
#   curl -fsSL https://hookbus.com/install.sh | bash
#   curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime claude-code
#   curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime skip --noninteractive
#
# Clones the bus, pulls HookBus + CRE-AgentProtect Light as public Docker
# images, bootstraps a bearer token, starts the stack, and optionally installs
# the publisher plugin for your chosen agent runtime.
#
# Idempotent. Safe to re-run. Apache 2.0 + MIT throughout.

set -euo pipefail

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
HOOKBUS_DIR="${HOOKBUS_DIR:-$HOME/hookbus-light}"
HOOKBUS_REPO="${HOOKBUS_REPO:-https://github.com/agentic-thinking/hookbus.git}"
RUNTIME="${RUNTIME:-}"
NONINTERACTIVE="${NONINTERACTIVE:-0}"
WITH_AGENTSPEND="${WITH_AGENTSPEND:-0}"
HOOKBUS_PORT="${HOOKBUS_PORT:-18800}"
AGENTSPEND_PORT="${AGENTSPEND_PORT:-8883}"

# Parse args (supported after `--` when piped from curl | bash)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime)        RUNTIME="${2:-}"; shift 2 ;;
    --runtime=*)      RUNTIME="${1#*=}"; shift ;;
    --noninteractive) NONINTERACTIVE=1; shift ;;
    --dir)            HOOKBUS_DIR="${2:-}"; shift 2 ;;
    --dir=*)          HOOKBUS_DIR="${1#*=}"; shift ;;
    --port)           HOOKBUS_PORT="${2:-}"; shift 2 ;;
    --port=*)         HOOKBUS_PORT="${1#*=}"; shift ;;
    --agentspend-port) AGENTSPEND_PORT="${2:-}"; shift 2 ;;
    --agentspend-port=*) AGENTSPEND_PORT="${1#*=}"; shift ;;
    --with-agentspend) WITH_AGENTSPEND=1; shift ;;
    --profile)
      profile="${2:-light}"
      case "$profile" in
        agentprotect|light|"") WITH_AGENTSPEND=0 ;;
        agentspend|full) WITH_AGENTSPEND=1 ;;
        *) printf "! Unknown profile '%s', using Light default\n" "$profile" >&2 ;;
      esac
      shift
      [[ $# -gt 0 ]] && shift ;;
    --profile=*)
      case "${1#*=}" in
        agentprotect|light|"") WITH_AGENTSPEND=0 ;;
        agentspend|full) WITH_AGENTSPEND=1 ;;
        *) printf "! Unknown profile '%s', using Light default\n" "${1#*=}" >&2 ;;
      esac
      shift ;;
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
Open-source event bus for AI agent lifecycle governance.
Apache 2.0 bus. CRE-AgentProtect Light adapter. Docker-based, 15 seconds to first event.
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
elif [[ -e "$HOOKBUS_DIR" ]]; then
  die "Install directory exists but is not a HookBus git checkout: $HOOKBUS_DIR
Choose a clean directory, for example:
  curl -fsSL https://hookbus.com/install.sh | bash -s -- --dir ./hookbus-light"
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
export HOOKBUS_PORT AGENTSPEND_PORT

# ----------------------------------------------------------------------------
# Start the stack
# ----------------------------------------------------------------------------
if [[ "$WITH_AGENTSPEND" = "1" ]]; then
  say "Starting HookBus + CRE-AgentProtect Light + AgentSpend..."
  COMPOSE_PROFILES=agentspend docker compose up -d 2>&1 | tail -10 || die "docker compose failed"
else
  say "Starting HookBus + CRE-AgentProtect Light..."
  docker compose up -d hookbus cre-agentprotect 2>&1 | tail -10 || die "docker compose failed"
fi

sleep 3
BUS_BASE="http://localhost:${HOOKBUS_PORT}"
if curl -sf -o /dev/null "$BUS_BASE/healthz" 2>/dev/null || \
   curl -sf -o /dev/null "$BUS_BASE/" 2>/dev/null || \
   curl -sf -o /dev/null -w "%{http_code}" "$BUS_BASE/" 2>/dev/null | grep -qE '^(200|401)$'; then
  ok "Bus responding on $BUS_BASE"
else
  warn "Bus not yet responding, may take a few more seconds on first boot."
fi

DASH_URL="$BUS_BASE/?token=${HOOKBUS_TOKEN}"

# ----------------------------------------------------------------------------
# Publisher selection
# ----------------------------------------------------------------------------
if [[ -z "$RUNTIME" && "$NONINTERACTIVE" = "0" ]]; then
  if [[ -t 0 ]]; then
    cat <<MENU

${C_BOLD}Which agent runtime do you want to wire into HookBus?${C_RESET}
  1) Claude Code (Anthropic, subprocess hook)
  2) Hermes      (Nous Research, Python plugin)
  3) OpenClaw    (Node plugin)
  4) Skip        (wire publishers manually later)

MENU
    read -r -p "Choice [1-4]: " choice
    case "$choice" in
      1) RUNTIME="claude-code" ;;
      2) RUNTIME="hermes" ;;
      3) RUNTIME="openclaw" ;;
      4|"") RUNTIME="skip" ;;
      *) warn "Unknown choice '$choice', skipping publisher step"; RUNTIME="skip" ;;
    esac
  else
    warn "No TTY for interactive prompt. Re-run with --runtime claude-code|hermes|openclaw|skip. Skipping for now."
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
    export HOOKBUS_URL=$BUS_BASE/event
    export HOOKBUS_TOKEN=$HOOKBUS_TOKEN
  (HOOKBUS_SOURCE defaults to "hermes-agent" inside the publisher;
   do NOT export HOOKBUS_SOURCE in your shell profile, it will leak
   into other publishers on the same host.)
HINT
}

install_claude_code() {
  say "Installing Claude Code publisher..."
  local TMP_DIR
  TMP_DIR=$(mktemp -d)

  git clone --quiet https://github.com/agentic-thinking/hookbus-publisher-claude-code.git "$TMP_DIR/src" || {
    warn "clone failed"; rm -rf "$TMP_DIR"; return 1
  }

  (cd "$TMP_DIR/src" && HOOKBUS_URL="$BUS_BASE/event" HOOKBUS_TOKEN="$HOOKBUS_TOKEN" ./install.sh 2>&1 | tail -20) \
    || warn "install.sh reported issues"
  rm -rf "$TMP_DIR"

  ok "Claude Code publisher installed."
  cat <<HINT
  Next: paste the hooks JSON block printed above into ~/.claude/settings.json
  (or merge with your existing hooks) and restart Claude Code.
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
    Environment="HOOKBUS_URL=$BUS_BASE/event"
    Environment="HOOKBUS_TOKEN=$HOOKBUS_TOKEN"
    Environment="HOOKBUS_FAIL_MODE=closed"
    Environment="HOOKBUS_SOURCE=openclaw"

  Then: systemctl --user daemon-reload && systemctl --user restart openclaw-gateway
HINT
}

case "$RUNTIME" in
  claude-code) install_claude_code || warn "Claude Code publisher install had issues." ;;
  hermes)      install_hermes      || warn "Hermes publisher install had issues." ;;
  openclaw)    install_openclaw    || warn "OpenClaw publisher install had issues." ;;
  skip|"")     warn "Skipped publisher install. See $HOOKBUS_REPO for the full shim table." ;;
  *)           warn "Unsupported runtime '$RUNTIME'. Accepted: claude-code, hermes, openclaw, skip." ;;
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
  ${C_BOLD}Profile:${C_RESET}    HookBus + CRE-AgentProtect Light$([[ "$WITH_AGENTSPEND" = "1" ]] && printf " + AgentSpend")

Smoke test a manual event:
  curl -s -H "Authorization: Bearer \$HOOKBUS_TOKEN" \\
       -H "Content-Type: application/json" \\
       -d '{"event_id":"test","event_type":"PreToolUse","timestamp":"'$(date -Iseconds)'","source":"manual","session_id":"smoke","tool_name":"ping","tool_input":{},"metadata":{}}' \\
       $BUS_BASE/event

DONE
