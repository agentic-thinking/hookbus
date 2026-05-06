# HookBus™

> **HookBus™, the agent-to-agent event bus.** Route governance-aware lifecycle events between any AI agent publisher and any subscriber, with priority-weighted deny-wins consolidation at the bus layer.

**Apache 2.0.** Production-ready. Zero runtime dependencies beyond Python stdlib + aiohttp + PyYAML.

![licence](https://img.shields.io/badge/licence-Apache%202.0-blue)
![python](https://img.shields.io/badge/python-3.10%2B-green)
![docker](https://img.shields.io/badge/docker-agentic-thinking%2Fhookbus-blue)

---

## Install (60 seconds)

One shell command opens a terminal UI, then clones the bus, pulls HookBus + CRE-AgentProtect Light as public Docker images, bootstraps a bearer token, starts the stack, and builds the local dashboard. The guided path can install the bus, add a publisher, run diagnostics, or send one safe smoke event. CRE-AgentProtect Light is a policy enforcement adapter for Microsoft AGT.

```bash
curl -fsSL https://hookbus.com/install.sh | bash
```

On a clean machine, use the terminal UI in two passes:

```bash
# 1. Install HookBus + CRE-AgentProtect Light
curl -fsSL https://hookbus.com/install.sh | bash

# Choose: 1) Install HookBus + CRE-AgentProtect Light

# 2. Re-run to add publishers to that bus
curl -fsSL https://hookbus.com/install.sh | bash

# Choose: 2) Add publisher to existing HookBus
```

Non-interactive variants:

```bash
# Claude Code users
curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime claude-code

# Codex CLI users
curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime codex

# Amp Code users
curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime amp

# OpenCode users
curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime opencode

# Hermes-agent users
curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime hermes

# OpenClaw users
curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime openclaw

# Bus + subscribers only, skip publisher
curl -fsSL https://hookbus.com/install.sh | bash -s -- --runtime skip --noninteractive

# Add a publisher to an existing HookBus install
curl -fsSL https://hookbus.com/install.sh | bash -s -- --publisher-only --runtime codex

# Check local install health
curl -fsSL https://hookbus.com/install.sh | bash -s -- --doctor

# Send one safe smoke event to an existing install
curl -fsSL https://hookbus.com/install.sh | bash -s -- --action test-event

# Clean side-by-side install when you already have ~/.hookbus or another stack
curl -fsSL https://hookbus.com/install.sh | bash -s -- --dir ./hookbus-light --port 18810 --runtime claude-code --noninteractive

# Optional cost monitor as well as AgentProtect
curl -fsSL https://hookbus.com/install.sh | bash -s -- --with-agentspend
```

The script prints the bus API URL, dashboard URL, and bearer token location on completion. Re-run any time, it is idempotent.

_Prefer not to pipe curl to bash? Inspect first:_ `curl -fsSL https://hookbus.com/install.sh > install.sh && less install.sh && bash install.sh`

---

## Prerequisites

- **Docker Engine 20+ with `docker compose` plugin**, Docker Desktop on macOS / Windows (with WSL2), or `curl -fsSL https://get.docker.com | sh` on Linux
- **Python 3.10+** with `pip`, needed only if you're installing a publisher shim. Available on most Linux distros; on Debian/Ubuntu/Mint run `sudo apt install python3-pip python3-venv`.

Tested on Linux (Ubuntu, Debian, Mint) and macOS. Windows via Docker Desktop + WSL2 is expected to work but is not validated at launch, please open an issue if you hit something.

## Manual install

If you prefer to see every step, or you are building an immutable / reproducible deployment, here is the full manual install.

Images ship as public containers on GitHub Container Registry. No build step, no registry login needed.

```bash
# 1. Clone the compose manifest
git clone https://github.com/agentic-thinking/hookbus.git
cd hookbus

# 2. Generate a stable bearer token (survives container restarts)
export HOOKBUS_TOKEN=$(openssl rand -base64 32 | tr -d '/+=')
docker compose up -d

# 3. Check the bus API
curl -H "Authorization: Bearer $HOOKBUS_TOKEN" http://localhost:18800/healthz
```

That pulls `ghcr.io/agentic-thinking/hookbus:latest` and `cre-agentprotect:latest`, starts the bus + AgentProtect Light, and wires bearer-token auth across the stack. To add AgentSpend, run `COMPOSE_PROFILES=agentspend docker compose up -d`.

**Want to build from source instead?** Clone the public repos side-by-side (`hookbus`, `cre-agentprotect`, and optionally `hookbus-agentspend`) then `docker build -t ghcr.io/agentic-thinking/<name>:latest .` in each before `docker compose up -d`.

Open `http://localhost:18800/?token=$HOOKBUS_TOKEN` in your browser to confirm the bus is alive and view the API links.

That brings up **HookBus™** + **CRE-AgentProtect Light** (Microsoft AGT policy adapter), talking via the Compose network. Auth is on by default, see the [Security](#security) section below for pinning your own token, reverse-proxy recipes, and production hardening.

## Install a publisher shim for your agent runtime

Publishers live in a separate package per agent. All shims read `HOOKBUS_URL` + `HOOKBUS_TOKEN` from the environment and send `Authorization: Bearer <token>` on every envelope they publish.

```bash
# Pick up the token from the container and export for your shell
export HOOKBUS_URL=http://localhost:18800/event
export HOOKBUS_TOKEN=$(docker compose exec -T hookbus cat /root/.hookbus/.token)
```

Then install the shim for your runtime:

| Agent runtime | One-command path | Repo | Licence |
|---|---|---|---|
| Claude Code | `curl -fsSL https://hookbus.com/install.sh \| bash -s -- --runtime claude-code` | [`hookbus-publisher-claude-code`](https://github.com/agentic-thinking/hookbus-publisher-claude-code) | MIT |
| Codex CLI | `curl -fsSL https://hookbus.com/install.sh \| bash -s -- --runtime codex` | [`hookbus-publisher-codex`](https://github.com/agentic-thinking/hookbus-publisher-codex) | MIT |
| Amp Code | `curl -fsSL https://hookbus.com/install.sh \| bash -s -- --runtime amp` | [`hookbus-publisher-amp`](https://github.com/agentic-thinking/hookbus-publisher-amp) | MIT |
| OpenCode | `curl -fsSL https://hookbus.com/install.sh \| bash -s -- --runtime opencode` | [`hookbus-publisher-opencode`](https://github.com/agentic-thinking/hookbus-publisher-opencode) | MIT |
| Nous Research Hermes | `curl -fsSL https://hookbus.com/install.sh \| bash -s -- --runtime hermes` | [`hookbus-publisher-hermes`](https://github.com/agentic-thinking/hookbus-publisher-hermes) | MIT |
| OpenClaw | `curl -fsSL https://hookbus.com/install.sh \| bash -s -- --runtime openclaw` | [`hookbus-publisher-openclaw`](https://github.com/agentic-thinking/hookbus-publisher-openclaw) | MIT |

Point the shim at your bus:

```bash
export HOOKBUS_URL=http://localhost:18800/event
```

Hermes note: `hermes-agent` loads `~/.hermes/.env` before the project `.env`, so the installer writes HookBus settings to both `~/.hermes/.env` and `~/hermes-agent/.env`. If Hermes is posting to an old port, re-run the installer or update both files.

Envelope spec is CC0 public domain, see Zenodo DOI `10.5281/zenodo.19642020`.

---

## What it does

1. **Publisher** emits a lifecycle event (`PreToolUse`, `PostLLMCall`, etc.) as a JSON envelope
2. **Bus** fans out the event to all registered subscribers in parallel
3. **Subscribers** return `allow` / `deny` / `ask` verdicts (sync subscribers) or record silently (async)
4. **Bus** consolidates verdicts, deny wins, and returns a single decision
5. The publisher acts on the consolidated verdict (tool proceeds, is blocked, or prompts user)

**Example in one turn:** a Hermes agent is about to run `rm -rf /important-dir`. The Hermes shim emits a `PreToolUse` event. A policy subscriber sees the destructive pattern and returns `deny`. The bus consolidates and returns `deny` to Hermes. The tool call never executes. The async audit subscriber records every step regardless of the verdict.

Optimised for sub-10ms P99 in local deployments.

---

## Canonical event types

| Event | When it fires | Typical subscribers |
|---|---|---|
| `UserPromptSubmit` | User enters a prompt | KB injector, session memory, prompt shield |
| `PreToolUse` | Agent about to call a tool | Policy engines, DLP filter |
| `PostToolUse` | Tool call returned | Audit log, cost tracker |
| `PreLLMCall` | LLM call about to happen | Prompt shield, budget check |
| `PostLLMCall` | LLM returned | Cost tracker (tokens, model, provider, reasoning) |
| `ModelResponse` | LLM finished generation | Transcript capture, provenance |
| `SessionStart` | New session began | Session memory, auditor |
| `SessionEnd` | Session ended | Session snapshot, cleanup |
| `AgentHandoff` | Agent delegating to another agent | Trace correlation, observability |
| `ErrorOccurred` | Something failed | Incident reporting, error tracker |

The bus never validates event types, it accepts any string, so new event types route automatically without bus code changes. (Earlier previews referenced a `Stop` event; use `SessionEnd` instead. Publishers that still send `Stop` will route fine, subscribers should treat it as equivalent to `SessionEnd`.)

Full envelope contract: see [`HOOKBUS_SPEC.md`](./HOOKBUS_SPEC.md).

---

## Example subscribers (shipped separately)

| Repo | Purpose | Licence |
|---|---|---|
| [cre-agentprotect](https://github.com/agentic-thinking/cre-agentprotect) | Policy enforcement via Microsoft Agent Governance Toolkit | MIT |
| [hookbus-agentspend](https://github.com/agentic-thinking/hookbus-agentspend) | Token + cost tracking with built-in dashboard | MIT |

Build your own subscriber in ~30 lines of Python, see [`HOOKBUS_SPEC.md`](./HOOKBUS_SPEC.md) for the envelope contract.

---

## Architecture

```
┌───────────────┐     envelope      ┌──────────────────┐
│   Publisher   │ ────────────────▶ │       Bus        │
│   (Hermes,    │                   │  (port 18800)    │
│   Claude,     │ ◀──────────────── │                  │
│   OpenClaw,   │  consolidated     │  ┌────────────┐  │
│   OpenAI …)   │  verdict          │  │ Dashboard  │  │
└───────────────┘                   │  │ (18801)    │  │
                                    │  └────────────┘  │
                                    └────┬────────┬────┘
                                         │        │
                       ┌─────────────────┘        └─────────────────┐
                       ▼                                            ▼
              ┌─────────────────┐                          ┌─────────────────┐
              │ cre-agentprotect│                          │   agentspend    │
              │  (sync policy)  │                          │ (async cost)    │
              └─────────────────┘                          └─────────────────┘
```

---

## Configuration

Register subscribers via `~/.hookbus/subscribers.yaml`:

```yaml
subscribers:
  - name: cre-agentprotect
    type: sync
    transport: http
    address: http://127.0.0.1:8898
    timeout: 5.0
    events: [PreToolUse, PostToolUse, PostLLMCall]
    metadata:
      vendor: Agentic Thinking Limited
      licence: MIT

  - name: agentspend
    type: async
    transport: http
    address: http://127.0.0.1:8883/event
    events: [PreToolUse, PostToolUse, PostLLMCall]
    metadata:
      vendor: Agentic Thinking Limited
      licence: MIT
      ui_port: 8883
```

See [`hookbus.yaml`](./hookbus.yaml) for the complete configuration reference.

By default the bus reads `~/.hookbus/subscribers.yaml`. Override with `HOOKBUS_CONFIG`:

```bash
export HOOKBUS_CONFIG=/etc/hookbus/subscribers.yaml
docker compose up -d
```

Useful for immutable deployments where the config lives alongside the service manifest.

---

## Patents

HookBus™ is the subject of UK Patent Application [GB2608069.7](https://www.ipo.gov.uk/) (bus architecture, filed 8 April 2026). See [NOTICE](./NOTICE) for full patent attribution.

Apache License 2.0 Section 3 grants you a patent licence for your use of this software. See [LICENSE](./LICENSE) and [NOTICE](./NOTICE) for full terms.

---

## What HookBus™ is not

HookBus is not a security perimeter against malicious AI agents. It assumes the agent is cooperating with its own hook surface, a hostile agent that actively tries to evade observation needs isolation at the runtime layer (sandbox, container, VM, seccomp), not the bus layer.

HookBus is not a replacement for network firewalls, secret scanners, code signing, or OS-level access control. It is a governance-aware event bus that makes the agent's own lifecycle decisions observable, consolidatable, and enforceable by external subscribers.

See the [architecture paper](https://doi.org/10.5281/zenodo.19642020) Section 15 for the full trust-model discussion.

---

## Troubleshooting

### I regenerated the bus token or changed ports and now Hermes keeps missing the bus

The shim reads `HOOKBUS_URL` and `HOOKBUS_TOKEN` from its environment. If you or
the wrapper wrote an old value into a `.env` file (for example
`~/.hermes/.env`), that file wins over the shell-exported value. After
`docker compose down -v && up -d` the bus writes a fresh token, but the shim may
still send the old token or post to an old port.

Fix:

```bash
# Remove any stale HookBus lines from Hermes env files
sed -i '/^HOOKBUS_/d' ~/.hermes/.env 2>/dev/null || true
sed -i '/^HOOKBUS_/d' ~/hermes-agent/.env 2>/dev/null || true

# Re-read the current token and export fresh values
export HOOKBUS_URL=http://localhost:18800/event
export HOOKBUS_TOKEN=$(docker compose exec -T hookbus cat /root/.hookbus/.token)
```

Then re-run the Hermes publisher installer:

```bash
curl -fsSL https://hookbus.com/install.sh | bash -s -- --publisher-only --runtime hermes
```

Events should land.

### AgentSpend container restarts repeatedly on first boot

AgentSpend is optional in HookBus Light. Start it with `COMPOSE_PROFILES=agentspend docker compose up -d`. It waits up to 30 seconds for the bus to write its token file to the
shared volume. If it still can't find a token after that, it exits. Check that
the `hookbus-auth` named volume is mounted on both the bus and the subscribers
(see `docker-compose.yml`).

### docker compose up fails with 'pull access denied'

You are running the quickstart before the images reached Docker Hub, or your
Docker daemon cannot reach Docker Hub. Build locally from the repo source:

```bash
docker compose up -d --build
```

---

## Security

HookBus generates a random authentication token on first start and requires it on **every** request to the bus and subscriber APIs. All data, events, token costs, AGT categories, session IDs, subscriber names, is protected. Unauthorised requests get `401 Unauthorized`.

### Read your token (one-time after install)

```bash
docker compose exec -T hookbus cat /root/.hookbus/.token
```

Copy the value. Examples below use `$TOKEN` to mean that string.

### Open the bus API

Paste the full URL with the token once:

```
http://localhost:18800/?token=<your-token>     # HookBus bus API links
http://localhost:8883/?token=<your-token>      # HookBus-AgentSpend dashboard, if enabled
```

The first load sets an HTTP-only cookie scoped to that host:port. Subsequent navigation in the same tab keeps you authenticated without the query param.

### Publishers authenticate via header

```bash
export HOOKBUS_TOKEN=$(docker compose exec -T hookbus cat /root/.hookbus/.token)
# now any shim reads HOOKBUS_TOKEN and sends Authorization: Bearer <token>
```

### Pin your own token (production)

For production, provide your own stable token in `docker-compose.yml` so it survives rebuilds:

```yaml
services:
  hookbus:
    environment:
      HOOKBUS_TOKEN: ${HOOKBUS_TOKEN:?set a long random token}
```

Then `HOOKBUS_TOKEN=$(openssl rand -base64 32) docker compose up -d`.

### Per-publisher tokens (multi-tenant)

For deployments with multiple agents (or multiple teams sharing one bus), issue one token per publisher with `HOOKBUS_TOKENS`. The bus resolves the bearer back to a `publisher_id` and stamps it onto `event.agent_id` so downstream subscribers can attribute every event to a known caller.

```yaml
services:
  hookbus:
    environment:
      # publisher_id:token pairs, comma-separated
      HOOKBUS_TOKENS: "hermes-prod:tok_AAA...,claude-code:tok_BBB...,openclaw:tok_CCC..."
```

`HOOKBUS_TOKEN` (single) and `HOOKBUS_TOKENS` (multi) can be combined, the single token falls through as the legacy publisher.

### Network binding

By default, ports bind to `0.0.0.0` so LAN hosts can reach the APIs, auth still enforces per-request. For stricter deployments, bind to `127.0.0.1` in `docker-compose.yml` and front with a reverse proxy (Caddy, nginx, Traefik) that handles TLS + auth at the edge.

### Disable auth (local dev only)

Mount an empty token file or set `HOOKBUS_TOKEN=` to empty, and the bus skips auth checks. Never do this on any machine with an internet-facing port.

---

## Commercial support

Production support, custom subscribers, compliance-grade audit evidence, and SLA-backed deployments are available from Agentic Thinking Limited. Contact [contact@agenticthinking.uk](mailto:contact@agenticthinking.uk).

---

## Contributing

PRs welcome under Apache 2.0. By submitting a pull request you agree to licence your contribution under the same terms (inbound = outbound). A lightweight CLA will be added ahead of the first external merge. See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for guidelines.

We especially welcome contributions for:

- New publisher shims (any agent runtime, Windsurf, Cursor, Continue, Cline, Amp extensions, etc.)
- Example subscribers (rate limiter, tool validator, prompt firewall, custom OPA adapter)
- Transport support beyond HTTP + Unix socket (NATS, Redis pubsub, gRPC)
- Language bindings for the envelope spec

---

## Trademarks

HookBus™ is a trademark of Agentic Thinking Limited. Nominative use (describing compatibility or integration) is always permitted. See [NOTICE](./NOTICE) for details.

## Specifications & citation

If you are publishing research or building on the HookBus architecture, the canonical reference is:

> Ruocco, P. (2026). *HookBus: A Governance-Aware Lifecycle Event Bus for Heterogeneous AI Agents.* Zenodo. https://doi.org/10.5281/zenodo.19642020

The envelope schema is released under CC0 (public domain). See [`HOOKBUS_SPEC.md`](./HOOKBUS_SPEC.md) in this repository for the in-tree specification.

---

Built by [Agentic Thinking Limited](https://agenticthinking.uk), UK Company 17152930.
Contact: contact@agenticthinking.uk
