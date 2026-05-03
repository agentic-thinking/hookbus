#!/usr/bin/env bash
# HookBus Light one-shot installer.
# Usage:
#   curl -fsSL https://hookbus.com/install.sh | bash
#   curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime claude-code
#   curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime codex
#   curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime amp
#   curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime opencode
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
ACTION="${ACTION:-}"
SEND_TEST_EVENT="${SEND_TEST_EVENT:-0}"
SKIP_STACK="${SKIP_STACK:-0}"

# Parse args (supported after `--` when piped from curl | bash)
while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime)        RUNTIME="${2:-}"; shift 2 ;;
    --runtime=*)      RUNTIME="${1#*=}"; shift ;;
    --action)         ACTION="${2:-}"; shift 2 ;;
    --action=*)       ACTION="${1#*=}"; shift ;;
    --send-test-event) SEND_TEST_EVENT=1; shift ;;
    --doctor)         ACTION="doctor"; shift ;;
    --publisher-only) ACTION="publisher"; SKIP_STACK=1; shift ;;
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

has_tty() { [[ -r /dev/tty && -w /dev/tty ]]; }

ask_tty() {
  local prompt="$1"
  local default="${2:-}"
  local answer
  if ! has_tty; then
    printf "%s" "$default"
    return 0
  fi
  if [[ -n "$default" ]]; then
    printf "%s [%s]: " "$prompt" "$default" > /dev/tty
  else
    printf "%s: " "$prompt" > /dev/tty
  fi
  IFS= read -r answer < /dev/tty || answer=""
  printf "%s" "${answer:-$default}"
}

main_menu() {
  cat > /dev/tty <<MENU

${C_BOLD}HookBus Setup${C_RESET}
  1) Install HookBus + CRE-AgentProtect Light
  2) Add publisher to existing HookBus
  3) Run doctor
  4) Send safe test event to existing HookBus
  5) Exit

MENU
  local choice
  choice=$(ask_tty "Choice" "1")
  case "$choice" in
    1) ACTION="install" ;;
    2) ACTION="publisher"; SKIP_STACK=1 ;;
    3) ACTION="doctor" ;;
    4) ACTION="test-event" ;;
    5) exit 0 ;;
    *) warn "Unknown choice '$choice', using install."; ACTION="install" ;;
  esac
}

select_runtime() {
  if [[ -n "$RUNTIME" || "$NONINTERACTIVE" = "1" ]]; then
    [[ -z "$RUNTIME" ]] && RUNTIME="skip"
    return 0
  fi

  if has_tty; then
    cat > /dev/tty <<MENU

${C_BOLD}Which agent runtime do you want to wire into HookBus?${C_RESET}
  1) Claude Code (Anthropic, subprocess hook)
  2) Codex CLI   (OpenAI, hook runner)
  3) Amp Code    (Sourcegraph, TypeScript plugin)
  4) OpenCode    (server plugin + wrapper)
  5) Hermes      (Nous Research, Python plugin)
  6) OpenClaw    (Node plugin)
  7) Skip        (wire publishers manually later)

MENU
    local choice
    choice=$(ask_tty "Choice" "7")
    case "$choice" in
      1) RUNTIME="claude-code" ;;
      2) RUNTIME="codex" ;;
      3) RUNTIME="amp" ;;
      4) RUNTIME="opencode" ;;
      5) RUNTIME="hermes" ;;
      6) RUNTIME="openclaw" ;;
      7|"") RUNTIME="skip" ;;
      *) warn "Unknown choice '$choice', skipping publisher step"; RUNTIME="skip" ;;
    esac
  else
    warn "No TTY for interactive prompt. Re-run with --runtime claude-code|codex|amp|opencode|hermes|openclaw|skip. Skipping for now."
    RUNTIME="skip"
  fi
}

load_existing_context() {
  ENV_FILE="$HOOKBUS_DIR/.env"
  [[ -f "$ENV_FILE" ]] || die "No HookBus env file found at $ENV_FILE. Run install first, or pass --dir to the existing HookBus install."
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a
  BUS_BASE="http://localhost:${HOOKBUS_PORT}"
  BUS_URL="$BUS_BASE/?token=${HOOKBUS_TOKEN}"
}

send_test_event() {
  say "Sending safe smoke event to $BUS_BASE..."
  local ts
  local event_id
  ts=$(date -Iseconds)
  event_id="smoke-$(date +%s)"
  curl -s -H "Authorization: Bearer $HOOKBUS_TOKEN" \
       -H "Content-Type: application/json" \
       -d '{"event_id":"'"$event_id"'","event_type":"PreToolUse","timestamp":"'"$ts"'","source":"hookbus-installer","session_id":"smoke","tool_name":"bash","tool_input":{"command":"echo hookbus-smoke-test"},"metadata":{"smoke_test":true}}' \
       "$BUS_BASE/event" >/dev/null || warn "safe smoke event failed"
  ok "Safe smoke event sent. API: $BUS_URL"
}

run_doctor() {
  cat <<DOCTOR

${C_BOLD}HookBus Doctor${C_RESET}
DOCTOR
  command -v docker >/dev/null && ok "docker found" || warn "docker not found"
  docker info >/dev/null 2>&1 && ok "docker daemon responding" || warn "docker daemon not responding"
  docker compose version >/dev/null 2>&1 && ok "docker compose found" || warn "docker compose plugin not found"
  command -v git >/dev/null && ok "git found" || warn "git not found"
  command -v openssl >/dev/null && ok "openssl found" || warn "openssl not found"
  command -v curl >/dev/null && ok "curl found" || warn "curl not found"
  if [[ -f "$HOOKBUS_DIR/.env" ]]; then
    load_existing_context
    ok "env file found: $ENV_FILE"
    local root_status
    root_status=$(curl -s -o /dev/null -w "%{http_code}" "$BUS_BASE/" 2>/dev/null || true)
    if curl -sf -o /dev/null "$BUS_BASE/healthz" 2>/dev/null || [[ "$root_status" =~ ^(200|401)$ ]]; then
      ok "bus responding: $BUS_BASE"
      ok "bus API: $BUS_URL"
    else
      warn "bus not responding on $BUS_BASE"
    fi
  else
    warn "env file not found: $HOOKBUS_DIR/.env"
  fi
}

# ----------------------------------------------------------------------------
# Banner
# ----------------------------------------------------------------------------
cat <<BANNER

${C_BOLD}HookBus Light installer${C_RESET}
Open-source event bus for AI agent lifecycle governance.
Apache 2.0 bus. CRE-AgentProtect Light adapter. Docker-based, 15 seconds to first event.
Docs: https://github.com/agentic-thinking/hookbus

BANNER

if [[ -z "$ACTION" && -z "$RUNTIME" && "$NONINTERACTIVE" = "0" ]] && has_tty; then
  main_menu
fi

ACTION="${ACTION:-install}"

if [[ "$ACTION" = "doctor" ]]; then
  run_doctor
  exit 0
fi

if [[ "$ACTION" = "test-event" ]]; then
  load_existing_context
  send_test_event
  exit 0
fi

if [[ "$ACTION" = "publisher" ]]; then
  SKIP_STACK=1
fi

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

command -v curl >/dev/null || \
  die "curl not found."

ok "Docker + compose + git + curl OK"

if [[ "$SKIP_STACK" = "1" ]]; then
  say "Using existing HookBus install at $HOOKBUS_DIR"
  load_existing_context
  ok "Existing bus context ready: $BUS_BASE"
else

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
  HOOKBUS_SUBSCRIBERS_FILE=./subscribers.with-agentspend.yaml COMPOSE_PROFILES=agentspend docker compose pull hookbus cre-agentprotect agentspend 2>&1 | tail -10 || warn "docker compose pull had issues; using local images"
  HOOKBUS_SUBSCRIBERS_FILE=./subscribers.with-agentspend.yaml COMPOSE_PROFILES=agentspend docker compose up -d 2>&1 | tail -10 || die "docker compose failed"
else
  say "Starting HookBus + CRE-AgentProtect Light..."
  docker compose pull hookbus cre-agentprotect 2>&1 | tail -10 || warn "docker compose pull had issues; using local images"
  docker compose up -d hookbus cre-agentprotect 2>&1 | tail -10 || die "docker compose failed"
fi

sleep 3
BUS_BASE="http://localhost:${HOOKBUS_PORT}"
ROOT_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BUS_BASE/" 2>/dev/null || true)
if curl -sf -o /dev/null "$BUS_BASE/healthz" 2>/dev/null || \
   [[ "$ROOT_STATUS" =~ ^(200|401)$ ]]; then
  ok "Bus responding on $BUS_BASE"
else
  warn "Bus not yet responding, may take a few more seconds on first boot."
fi

BUS_URL="$BUS_BASE/?token=${HOOKBUS_TOKEN}"

fi

# ----------------------------------------------------------------------------
# Publisher selection
# ----------------------------------------------------------------------------
select_runtime

# ----------------------------------------------------------------------------
# Install chosen publisher
# ----------------------------------------------------------------------------
install_hermes() {
  say "Installing Hermes publisher..."

  local TMP_DIR
  TMP_DIR=$(mktemp -d)

  git clone --quiet --depth 1 https://github.com/agentic-thinking/hookbus-publisher-hermes.git "$TMP_DIR/src" || {
    warn "clone failed"; rm -rf "$TMP_DIR"; return 1
  }

  (cd "$TMP_DIR/src" && HOOKBUS_URL="$BUS_BASE/event" HOOKBUS_TOKEN="$HOOKBUS_TOKEN" HOOKBUS_FAIL_MODE=open bash install.sh 2>&1 | tail -24) || {
    warn "Hermes publisher install had issues"; rm -rf "$TMP_DIR"; return 1
  }
  rm -rf "$TMP_DIR"

  ok "Hermes publisher installed."
  cat <<HINT
  Next: restart Hermes and run:
    hermes chat --tui

  The publisher installer writes HookBus settings to:
    $HOME/hermes-agent/.env
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

install_codex() {
  say "Installing Codex CLI publisher..."
  if ! command -v node >/dev/null; then
    warn "node is required for the Codex publisher. Install Node.js 18+ and re-run with --runtime codex."
    return 1
  fi

  local TMP_DIR
  TMP_DIR=$(mktemp -d)

  git clone --quiet --depth 1 https://github.com/agentic-thinking/hookbus-publisher-codex.git "$TMP_DIR/src" || {
    warn "clone failed"; rm -rf "$TMP_DIR"; return 1
  }

  (cd "$TMP_DIR/src" && HOOKBUS_URL="$BUS_BASE/event" HOOKBUS_TOKEN="$HOOKBUS_TOKEN" ./install.sh 2>&1 | tail -24) \
    || warn "install.sh reported issues"
  rm -rf "$TMP_DIR"

  ok "Codex CLI publisher installed."
  cat <<HINT
  Important: fully quit and restart Codex. Already-running Codex sessions
  do not reload hooks.

  Verify:
    $HOME/.local/bin/codex-gate --doctor
HINT
}

install_amp() {
  say "Installing Amp publisher..."
  local TMP_DIR
  TMP_DIR=$(mktemp -d)

  git clone --quiet --depth 1 https://github.com/agentic-thinking/hookbus-publisher-amp.git "$TMP_DIR/src" || {
    warn "clone failed"; rm -rf "$TMP_DIR"; return 1
  }

  (cd "$TMP_DIR/src" && HOOKBUS_URL="$BUS_BASE/event" HOOKBUS_TOKEN="$HOOKBUS_TOKEN" HOOKBUS_FAIL_MODE=open bash install.sh <<< 'n' 2>&1 | tail -14) || {
    warn "Amp publisher install had issues"; rm -rf "$TMP_DIR"; return 1
  }
  rm -rf "$TMP_DIR"

  ok "Amp publisher installed."
  cat <<HINT
  Next: launch Amp with the HookBus plugin enabled:
    amp-hookbus

  Plain 'amp' remains unaffected. Config lives at:
    $HOME/.config/amp/plugins/hookbus.env
HINT
}

install_opencode() {
  say "Installing OpenCode publisher..."
  if ! command -v node >/dev/null; then
    warn "node is required for the OpenCode publisher. Install Node.js 18+ and re-run with --runtime opencode."
    return 1
  fi

  local TMP_DIR
  TMP_DIR=$(mktemp -d)

  git clone --quiet --depth 1 https://github.com/agentic-thinking/hookbus-publisher-opencode.git "$TMP_DIR/src" || {
    warn "clone failed"; rm -rf "$TMP_DIR"; return 1
  }

  (cd "$TMP_DIR/src" && HOOKBUS_URL="$BUS_BASE/event" HOOKBUS_TOKEN="$HOOKBUS_TOKEN" HOOKBUS_FAIL_MODE=open bash install.sh 2>&1 | tail -18) || {
    warn "OpenCode publisher install had issues"; rm -rf "$TMP_DIR"; return 1
  }
  rm -rf "$TMP_DIR"

  ok "OpenCode publisher installed."
  cat <<HINT
  Next: launch OpenCode normally:
    opencode

  Or run a deterministic smoke test:
    opencode-agenthook run "Reply OK"
HINT
}

install_openclaw() {
  say "Installing OpenClaw publisher..."
  if ! command -v npm >/dev/null; then
    warn "npm not found. Install Node.js 18+ and re-run with --runtime openclaw."
    return 1
  fi

  local TMP_DIR
  TMP_DIR=$(mktemp -d)

  git clone --quiet --depth 1 https://github.com/agentic-thinking/hookbus-publisher-openclaw.git "$TMP_DIR/src" || {
    warn "clone failed"; rm -rf "$TMP_DIR"; return 1
  }

  (cd "$TMP_DIR/src" && HOOKBUS_URL="$BUS_BASE/event" HOOKBUS_TOKEN="$HOOKBUS_TOKEN" HOOKBUS_FAIL_MODE=closed HOOKBUS_SOURCE=openclaw bash install.sh 2>&1 | tail -24) || {
    warn "OpenClaw publisher install had issues"; rm -rf "$TMP_DIR"; return 1
  }
  rm -rf "$TMP_DIR"

  ok "OpenClaw publisher installed."
  cat <<HINT
  Next: launch OpenClaw normally:
    openclaw tui

  The publisher installer writes plugin config to:
    $HOME/.openclaw/extensions/cre/hookbus.env
HINT
}

case "$RUNTIME" in
  claude-code) install_claude_code || warn "Claude Code publisher install had issues." ;;
  codex)       install_codex       || warn "Codex publisher install had issues." ;;
  amp)         install_amp         || warn "Amp publisher install had issues." ;;
  opencode)    install_opencode    || warn "OpenCode publisher install had issues." ;;
  hermes)      install_hermes      || warn "Hermes publisher install had issues." ;;
  openclaw)    install_openclaw    || warn "OpenClaw publisher install had issues." ;;
  skip|"")     warn "Skipped publisher install. See $HOOKBUS_REPO for the full shim table." ;;
  *)           warn "Unsupported runtime '$RUNTIME'. Accepted: claude-code, codex, amp, opencode, hermes, openclaw, skip." ;;
esac

if [[ "$SEND_TEST_EVENT" = "1" ]]; then
  send_test_event
fi

# ----------------------------------------------------------------------------
# Final summary
# ----------------------------------------------------------------------------
cat <<DONE

${C_G}${C_BOLD}HookBus Light is running.${C_RESET}

  ${C_BOLD}Bus API:${C_RESET}   $BUS_URL
  ${C_BOLD}Token:${C_RESET}      saved in $ENV_FILE (chmod 600)
  ${C_BOLD}Compose:${C_RESET}    cd $HOOKBUS_DIR && docker compose ps
  ${C_BOLD}Stop:${C_RESET}       cd $HOOKBUS_DIR && docker compose down
  ${C_BOLD}Docs:${C_RESET}       https://github.com/agentic-thinking/hookbus
  ${C_BOLD}Profile:${C_RESET}    HookBus + CRE-AgentProtect Light$([[ "$WITH_AGENTSPEND" = "1" ]] && printf " + AgentSpend")

Install another publisher against this bus:
  curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime claude-code
  curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime codex
  curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime amp
  curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime opencode

Smoke test a manual event:
  set -a
  source "$ENV_FILE"
  set +a
  curl -s -H "Authorization: Bearer \$HOOKBUS_TOKEN" \\
       -H "Content-Type: application/json" \\
       -d '{"event_id":"test","event_type":"PreToolUse","timestamp":"'$(date -Iseconds)'","source":"manual","session_id":"smoke","tool_name":"ping","tool_input":{},"metadata":{}}' \\
       $BUS_BASE/event

DONE
