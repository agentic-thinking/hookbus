#!/usr/bin/env bash
# hookbus-token-sync , one-command refresh of the HookBus bearer token across
# every known consumer on this machine.
#
# Use this after:
#   - `docker compose down -v && up -d` (bus regenerates its token)
#   - First-time install (populate every shim .env from the fresh token)
#   - Any time a publisher shim is silently getting 401s
#
# What it does:
#   1. Reads the current token from the running HookBus container
#   2. Writes HOOKBUS_TOKEN into every known consumer location, overwriting
#      any stale value. Missing files are skipped (we don't create them).
#   3. Prints a per-target summary so you know what was touched.
#
# Consumer locations checked (add new ones at the top of known_targets()):
#   ~/.hermes/.env                           , hermes CLI
#   ~/hermes-agent/.env                      , hermes-agent dir
#   ~/.openclaw/config.env                   , OpenClaw runtime env
#   ~/.config/systemd/user/openclaw-gateway.service.d/hookbus.conf
#                                            , OpenClaw systemd drop-in
#   ~/.claude/settings.json                  , Claude Code hooks (scanned only)
#   (plus any path you pass via --extra)
#
# Override the container name with HOOKBUS_CONTAINER=<name>.
# Override the in-container token path with HOOKBUS_TOKEN_PATH=<path>.

set -euo pipefail

HOOKBUS_CONTAINER="${HOOKBUS_CONTAINER:-hookbus}"
HOOKBUS_TOKEN_PATH_IN_CONTAINER="${HOOKBUS_TOKEN_PATH:-/root/.hookbus/.token}"
EXTRA_TARGETS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --extra) EXTRA_TARGETS+=("$2"); shift 2 ;;
        --help|-h)
            sed -n '2,30p' "$0"
            exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

say()  { printf "\033[1;32m[hookbus-token-sync]\033[0m %s\n" "$*" >&2; }
warn() { printf "\033[1;33m[hookbus-token-sync]\033[0m %s\n" "$*" >&2; }
die()  { printf "\033[1;31m[hookbus-token-sync] error:\033[0m %s\n" "$*" >&2; exit 1; }

# 1. Read the current token from the running container.
if ! command -v docker >/dev/null 2>&1; then
    die "docker not found on PATH"
fi
if ! TOKEN="$(docker exec "$HOOKBUS_CONTAINER" cat "$HOOKBUS_TOKEN_PATH_IN_CONTAINER" 2>/dev/null)"; then
    die "could not read token from container '$HOOKBUS_CONTAINER' at '$HOOKBUS_TOKEN_PATH_IN_CONTAINER'. Is the bus running?"
fi
if [[ -z "$TOKEN" ]]; then
    die "token file is empty. The bus may not have finished initialising yet."
fi

say "fresh token: ${TOKEN:0:8}...${TOKEN: -4}  (${#TOKEN} chars)"

# 2. Known consumer targets. Each entry is a file path whose format we know.
#    If the file doesn't exist, we skip it (we don't create files from nothing).

update_envfile() {
    # Strip any existing HOOKBUS_TOKEN line, then append the fresh one.
    local f="$1"
    if [[ ! -f "$f" ]]; then
        return 1
    fi
    sed -i.bak '/^HOOKBUS_TOKEN=/d' "$f"
    rm -f "${f}.bak"
    printf 'HOOKBUS_TOKEN=%s\n' "$TOKEN" >> "$f"
    say "updated envfile: $f"
    return 0
}

update_systemd_dropin() {
    # The drop-in format is:
    #   [Service]
    #   Environment=HOOKBUS_TOKEN=<value>
    local f="$1"
    if [[ ! -f "$f" ]]; then
        return 1
    fi
    # Rewrite the file cleanly (small file, safe to regenerate).
    cat > "$f" <<EOF
[Service]
Environment=HOOKBUS_TOKEN=$TOKEN
EOF
    say "updated systemd drop-in: $f"
    # Reload + restart the service so it picks up the new env.
    local unit
    unit="$(basename "$(dirname "$f")" | sed 's/\.service\.d$//')"
    if [[ -n "$unit" ]]; then
        systemctl --user daemon-reload 2>/dev/null || true
        systemctl --user restart "$unit" 2>/dev/null && say "restarted user unit: $unit" || warn "could not restart user unit: $unit"
    fi
    return 0
}

touched=0
skipped=0

for ef in "$HOME/.hermes/.env" "$HOME/hermes-agent/.env" "$HOME/.openclaw/config.env"; do
    if update_envfile "$ef"; then
        touched=$((touched + 1))
    else
        skipped=$((skipped + 1))
    fi
done

for sd in "$HOME/.config/systemd/user/openclaw-gateway.service.d/hookbus.conf"; do
    if update_systemd_dropin "$sd"; then
        touched=$((touched + 1))
    else
        skipped=$((skipped + 1))
    fi
done

for extra in "${EXTRA_TARGETS[@]}"; do
    if update_envfile "$extra"; then
        touched=$((touched + 1))
    else
        warn "extra target not found: $extra"
        skipped=$((skipped + 1))
    fi
done

say "done. touched=$touched  skipped=$skipped"
say "shell: export HOOKBUS_TOKEN=\"\$(docker exec $HOOKBUS_CONTAINER cat $HOOKBUS_TOKEN_PATH_IN_CONTAINER)\""
