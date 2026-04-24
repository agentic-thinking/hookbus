# HookBus - Universal Event Bus for AI Agent Lifecycle Enforcement

**Version:** 1.0 (Draft)
**Date:** 2026-04-08
**Licence:** Apache 2.0

---

## What HookBus Is

A central router that sits between AI agents and their tool execution. Every lifecycle event (tool call, session start, user message, error) flows through the bus. The bus routes events to registered subscribers. Subscribers decide what to do: block, allow, log, alert, learn, count.

The bus has no opinion on content. It does not know what PreToolUse means. It does not know what "deny" means. It routes events and collects decisions. That is all.

## Glossary

| Term | Definition |
|---|---|
| **Bus** | The central router. Receives events from publishers, routes to subscribers. Has no opinion on content. Just routes. |
| **Publisher** | Any source of lifecycle events. An AI assistant, an SDK, an HTTP client. Sends events TO the bus. |
| **Subscriber** | Any service that receives events FROM the bus. Can be sync (returns a decision) or async (fire and forget). |
| **Sync Subscriber** | Blocks execution until it responds. Returns allow/deny/ask. Example: a policy subscriber. |
| **Async Subscriber** | Receives events without blocking. Does not return a decision. Example: audit logger, Slack alerts. |
| **Lifecycle Event** | A moment in the AI agent's execution. Something happened or is about to happen. Identified by its event type. |
| **Event Type** | PascalCase string identifying what happened. PreToolUse, PostToolUse, SessionStart. The bus is agnostic, routes any string. |
| **Event** | A single JSON message containing: event_id, event_type, timestamp, source, session_id, tool_name, tool_input, metadata. |
| **Decision** | A sync subscriber's response: allow, deny, or ask. The bus consolidates decisions from all sync subscribers (deny wins). |
| **Source** | Which assistant or SDK published the event. claude-code, cursor, openai-sdk, langchain. |
| **Session** | One continuous interaction between a user and an AI assistant. Has a session_id. |
| **Thin Client** | Lightweight process that hooks call. Normalises raw assistant input into the standard event format and sends to the bus. |
| **Transport** | How a subscriber connects: unix_socket, http, or in_process (Python class loaded by the bus). |
| **Event Filter** | Which event types a subscriber wants. ["PreToolUse"] or ["*"] for everything. |
| **Normalisation** | Converting raw assistant hook names to standard PascalCase. on_tool_start becomes PreToolUse. Done by the thin client, not the bus. |
| **Fan-out** | Bus sending one event to multiple subscribers in parallel. |
| **Deny-wins** | Consolidation rule for sync subscribers. If any subscriber says deny, the final decision is deny. |
| **Hot Reload** | Bus watches subscriber config. Add or remove subscribers without restarting. |
| **Override** | A human approval mechanism. Subscriber-specific. The bus does not know about overrides. |
| **Metadata** | Arbitrary key-value pairs on events or responses. Extensible without protocol changes. |

---

## Architecture

```
Publishers                          Bus                         Subscribers
                                     |
Claude Code hook  -->                |   --> [SYNC]  Policy Subscriber (allow/deny/ask)
Cursor hook       -->                |   --> [SYNC]  Budget Gate (token/cost limits)
Copilot hook      -->    HookBus     |   --> [ASYNC] Audit Logger (SQLite trail)
OpenAI SDK hook   -->    (router)    |   --> [ASYNC] Token Counter (cost tracking)
Anthropic SDK     -->                |   --> [ASYNC] Slack Alerts (deny notifications)
LangChain hook    -->                |   --> [ASYNC] Learning Engine (rule derivation)
HTTP webhook      -->                |   --> [ASYNC] SIEM (Splunk, Datadog forward)
                                     |
```

Publishers do not know about subscribers. Subscribers do not know about publishers. The bus is the only component that knows both. Add a publisher, every subscriber gets its events. Add a subscriber, every publisher's events flow to it.

---

## Bus Database

The bus is primarily stateless but maintains minimal operational state in memory (not persisted to disk):

| Operational State | Purpose | Storage |
|---|---|---|
| Pending responses | Track which sync subscribers have not yet responded | In-memory dict, TTL per event |
| Subscriber health | Last heartbeat, failure count, circuit breaker state | In-memory dict |
| Session correlation | Link PreToolUse to PostToolUse for the same tool call | In-memory dict, TTL 60s |
| Event sequence | Ordering guarantee within a session | In-memory counter per session_id |

This is NOT a database. It is ephemeral process memory. If the bus restarts, this state is lost and rebuilt from the next incoming events. No persistence, no recovery, no backup.

Subscriber data (rules, audit logs, costs) belongs to subscribers. The bus never stores subscriber data.

---

## Subscriber Health and Circuit Breakers

The bus monitors subscriber health:

- **Heartbeat:** Bus pings each subscriber every 10s. Three missed heartbeats = mark unhealthy.
- **Circuit breaker:** If a sync subscriber fails 3 times in 60s, circuit opens. Bus skips that subscriber for 30s, then tries again (half-open). If it works, circuit closes.
- **Timeout:** Sync subscribers must respond within their configured timeout (default 30s). Timeout = failure.
- **Fail mode:** Configurable per subscriber. fail-open (allow if subscriber is down) or fail-closed (deny if subscriber is down). CRE should be fail-closed. Token counter should be fail-open.



---

## Decision Consolidation (v2)

Deny-wins is the default but the bus supports priority-weighted decisions:



Each sync subscriber has a priority (default 100). If two subscribers disagree, higher priority wins. If equal priority, deny wins (safety default).



---

## Event Format

```json
{
  "schema_version": 1,
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "event_type": "PreToolUse",
  "timestamp": "2026-04-08T04:30:00.000Z",
  "source": "claude-code",
  "agent_id": "hermes-prod",
  "session_id": "abc123",
  "correlation_id": "run-7f3b...",
  "tool_name": "Bash",
  "tool_input": {"command": "git push --force origin main"},
  "metadata": {},
  "annotations": {}
}
```

| Field | Required | Purpose |
|---|---|---|
| `schema_version` | no (defaults to `1`) | Wire-format version (integer), bumped only on breaking envelope changes |
| `event_id` | yes | UUID, unique per event |
| `event_type` | yes | Canonical name (see below) |
| `timestamp` | yes | ISO-8601 UTC |
| `source` | yes | Publisher family, e.g. `claude-code`, `openai-sdk`, `hermes` |
| `agent_id` | no | Specific publisher instance. Bus always stamps this from the resolved publisher_id. Under `HOOKBUS_TOKENS` the value is the configured publisher name; under single-token `HOOKBUS_TOKEN` it defaults to `legacy` (override with `HOOKBUS_LEGACY_PUBLISHER_ID`). See Publisher Integration. |
| `session_id` | no | Groups events in one agent run |
| `correlation_id` | no | Links related events across sessions (multi-agent handoff, retries) |
| `tool_name` | for `PreToolUse` / `PostToolUse` | Tool being called |
| `tool_input` | no | Arbitrary JSON, bus does not validate |
| `metadata` | no | Hook-specific structured data (see Standard Metadata Keys) |
| `annotations` | no | Free-form subscriber/operator notes, bus never reads |

Convention: subscribers that write annotations scope their keys under a `subscribers.<name>` prefix (e.g. `annotations.subscribers.dlp-filter`) so unrelated subscribers do not stomp on each other. `hookbus.protocol` exposes `set_annotation` / `get_annotation` helpers that apply this prefix for you.

All events use this format regardless of which publisher sent them. The thin client normalises before sending.

---

## Event Types

### Known Today

| Event Type | Phase | Description |
|---|---|---|
| PreToolUse | Before | Tool call is about to execute |
| PostToolUse | After | Tool call completed |
| UserPromptSubmit | Before | User sent a message |
| PreLLMCall | Before | LLM API call about to be issued (budget check, prompt shield) |
| PostLLMCall | After | LLM API call returned (cost tracker, transcript capture) |
| ModelResponse | After | LLM finished generation (transcript, provenance) |
| SessionStart | State | New session began |
| SessionEnd | State | Session ended |
| AgentHandoff | During | Agent delegating to another agent |
| ErrorOccurred | After | Something failed |

### Rules

- PascalCase, no underscores, no dots
- Verb first (Pre, Post, On) or noun if state event (SessionStart, ErrorOccurred)
- The bus never validates event types. It accepts any string.
- New event types from assistants route automatically without bus code changes.

### Normalisation Map (thin client)

| Source | Raw name | Normalised |
|---|---|---|
| Claude Code | PreToolUse | PreToolUse |
| OpenAI SDK | on_tool_start | PreToolUse |
| OpenAI SDK | on_tool_end | PostToolUse |
| Anthropic SDK | tool_use_callback | PreToolUse |
| LangChain | on_tool_start | PreToolUse |
| CrewAI | step_callback | PreToolUse |

Normalisation happens in the thin client, never in the bus.

---

## Standard Metadata Keys

`metadata` is free-form, but the following keys are canonical across subscribers. Shims SHOULD emit them when the data is available, subscribers MAY rely on them.

### `PostLLMCall`

| Key | Type | Purpose |
|---|---|---|
| `model` | string | Model id as returned by the provider (e.g. `MiniMax-M2.7`, `claude-opus-4-7`, `moonshotai/kimi-k2.6-20260420`) |
| `provider` | string | `anthropic`, `openai`, `minimax`, `openrouter`, `bedrock`, `azure`, `vertex`, etc. |
| `tokens_input` | int | Input tokens counted by the provider |
| `tokens_output` | int | Output tokens counted by the provider |
| `total_tokens` | int | Convenience sum |
| `reasoning_content` | string or null | Full reasoning text when the provider exposed it (Anthropic thinking blocks, OpenAI-compat `reasoning_content`, MiniMax `reasoning_details`). Truncated to 64k chars by `hookbus.publisher_helpers.truncate_reasoning` |
| `reasoning_chars` | int | Length of the original reasoning before any truncation. Useful for filtering/indexing without loading the full payload |
| `response_content` | string | Reply text delivered to the caller (truncated to ~4k chars in typical subscribers) |

`reasoning_content` + `response_content` together give regulators a complete transcript of the LLM exchange, which is what EU AI Act Art 12 record-keeping for high-risk AI systems requires.

### `PreToolUse` / `PostToolUse`

| Key | Type | Purpose |
|---|---|---|
| `estimated_usd` | float | Cost estimate from a budget subscriber, stamped onto the event so downstream consumers see a single authoritative figure |
| `duration_ms` | int | Tool wall-clock (for `PostToolUse`) |
| `exit_code` | int | Tool exit code (for `PostToolUse`) |

Subscribers can add arbitrary keys, but picking a name that collides with the canonical list will confuse other subscribers that trust the canonical meaning.

---

## Tool Inputs

| Tool | Input fields |
|---|---|
| Bash/shell | {"command": "..."} |
| Write | {"file_path": "...", "content": "..."} |
| Edit | {"file_path": "...", "old_string": "...", "new_string": "..."} |
| Read | {"file_path": "..."} |
| WebFetch | {"url": "...", "prompt": "..."} |
| WebSearch | {"query": "..."} |
| Agent | {"prompt": "...", "subagent_type": "..."} |
| Custom/SDK | {"command": "..."} or arbitrary JSON |

The bus does not validate inputs. It passes them through. Subscribers decide what matters.

---

## Subscriber Config

```yaml
# ~/.hookbus/subscribers.yaml
subscribers:
  - name: cre-gate
    type: sync
    transport: unix_socket
    address: /home/user/.cre/subscriber.sock
    timeout: 30
    events: ["PreToolUse"]

  - name: cre-agentprotect
    type: sync
    transport: http
    address: http://cre-agentprotect:8878
    timeout: 5.0
    events: [PreToolUse, PostToolUse, PostLLMCall]
    events: ["*"]

  - name: token-counter
    type: async
    transport: http
    address: http://localhost:9100/events
    events: ["PreToolUse", "PostToolUse"]

  - name: slack-alerts
    type: async
    transport: http
    address: http://localhost:9200/events
    events: ["PreToolUse"]
    filter: "decision == deny"
```

### Subscriber Types

| Type | Behaviour | Returns decision? |
|---|---|---|
| sync | Bus waits for response before returning to caller | Yes (allow/deny/ask) |
| async | Bus sends event and does not wait | No |

### Transports

| Transport | For | Example |
|---|---|---|
| unix_socket | Local out-of-process subscribers | Policy subscriber running out-of-process |
| http | Remote or third-party subscribers | Slack, Datadog, PagerDuty |
| in_process | Simple Python class loaded by bus | Audit logger, token counter |

### Config Path

The bus reads subscribers from `~/.hookbus/subscribers.yaml` by default. Set `HOOKBUS_CONFIG` to point at a different path, useful for immutable deployments where the config ships alongside the service manifest:

```bash
export HOOKBUS_CONFIG=/etc/hookbus/subscribers.yaml
```

### Hot Reload

Bus watches subscribers.yaml with inotify. Add or remove subscribers without restarting. Zero downtime.

---

## Subscriber Response Format

```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "subscriber": "policy-subscriber",
  "decision": "deny",
  "reason": "Force push blocked by policy",
  "metadata": {}
}
```

Async subscribers do not return responses.

---

## Decision Consolidation

```
Event arrives at bus
    |
    v
Fan out to ALL matching subscribers in parallel
    |
    +--> SYNC subscribers: send event, wait for response (up to timeout)
    +--> ASYNC subscribers: send event, do not wait
    |
    v
Collect sync responses:
    - Any DENY?   -> return DENY (deny wins)
    - Any ASK?    -> return ASK
    - All ALLOW?  -> return ALLOW
    - Timeout?    -> configurable: fail-open or fail-closed
    |
    v
Return consolidated decision to caller
```

Deny wins. Always. If one sync subscriber says deny and ten say allow, the answer is deny.

---

## Publisher Integration

### Authentication

The bus accepts a bearer token on every envelope. Publishers send `Authorization: Bearer <token>` on their POST to `/event`. Two modes:

| Env var | Use case | Semantics |
|---|---|---|
| `HOOKBUS_TOKEN` | Single publisher | One shared token. Any envelope with that token is accepted. `event.agent_id` is untouched. |
| `HOOKBUS_TOKENS` | Multi-tenant | Comma-separated `publisher_id:token` pairs. The bus resolves the presented bearer back to a `publisher_id` and stamps it onto `event.agent_id`. Subscribers can attribute every event to a known caller without trusting publisher-supplied `agent_id`. |

```yaml
# docker-compose.yml — multi-tenant bus
services:
  hookbus:
    environment:
      HOOKBUS_TOKENS: "hermes-prod:tok_AAA...,claude-code:tok_BBB...,openclaw:tok_CCC..."
```

Both can be set at once. The bus resolves multi-tenant first (`HOOKBUS_TOKENS`), then falls through to the single-token check (`HOOKBUS_TOKEN`) on miss. Supported together primarily for migration scenarios - bring up the bus with single-token, then swap publishers to per-publisher tokens one at a time. Steady-state production should pick one mode.

### Environment reference (bus)

The bus reads these env vars at startup. Keep in sync with `docker-compose.yml` / your process manager.

| Env var | Default | Purpose |
|---|---|---|
| `HOOKBUS_TOKEN` | generated | Single shared bearer token. Legacy mode. |
| `HOOKBUS_TOKENS` | unset | `publisher_id:token,...` map. Multi-tenant auth + `agent_id` stamping. |
| `HOOKBUS_LEGACY_PUBLISHER_ID` | `legacy` | `agent_id` value stamped for legacy-token callers. |
| `HOOKBUS_TOKEN_PATH` | `/root/.hookbus/.token` | Where the bus persists the generated token. |
| `HOOKBUS_CONFIG` | `~/.hookbus/subscribers.yaml` | Path to the subscriber configuration YAML. |
| `HOOKBUS_STRICT_REASONING` | `off` | `off` \| `warn` \| `reject`. Validator for `reasoning_content` on `PostLLMCall`. Invalid values abort startup. |
| `HOOKBUS_STRICT_CORRELATION` | `off` | `off` \| `warn` \| `reject`. Validator for `correlation_id` on `Pre*` events. |
| `HOOKBUS_SUBSCRIBER_ALLOW_PRIVATE` | `0` | Set to `1` to permit subscriber URLs resolving to private/loopback IPs (Compose/K8s deployments). Cloud metadata endpoints remain blocked unconditionally. |

Rollout pattern: new validators start at `off` so existing publishers don\'t break; flip to `warn` during migration, then `reject` once every publisher ships the new wire format.

### Publisher helpers (Python)

Shim authors can reuse the common plumbing rather than reimplement it per-vendor:

```python
from hookbus.publisher_helpers import extract_reasoning, truncate_reasoning

reasoning, reasoning_chars, reply = extract_reasoning(response, provider="minimax")
# -> ("...CoT text...", 1843, "final reply to user")
```

`extract_reasoning` handles every LLM response shape we currently see on the bus: Anthropic thinking blocks; OpenAI-compat `reasoning_content` / `reasoning` / `reasoning_details` (covers Kimi, MiniMax, Z.AI GLM, OpenRouter, Gemini, Hermes, Amp, claude-code); and the OpenAI Agents SDK `ModelResponse` shape. Returns `(reasoning_content, reasoning_chars, reply_text)`. Does not intentionally raise for missing fields - a shape we haven\'t seen returns `(None, 0, "")` rather than erroring.

### AI Assistants (auto-wire)

```bash
hookbus connect claude-code
hookbus connect cursor
hookbus connect augment
```

CLI detects assistant hook config location, writes the hook pointing at the bus thin client.

### SDK Hooks (one line)

```python
# OpenAI Agents SDK
from hookbus import hooks
result = await Runner.run(agent, prompt, hooks=hooks())

# LangChain
from hookbus import callback
chain.invoke(input, config={"callbacks": [callback()]})

# Anthropic
from hookbus import tool_callback
agent = Agent(tools=[...], tool_use_callback=tool_callback)
```

### HTTP (any tool)

```bash
curl -X POST http://localhost:18800/event \
  -H "Content-Type: application/json" \
  -d '{"event_type":"PreToolUse","tool_name":"Bash","tool_input":{"command":"rm -rf /"}}'
```

Any tool that can POST JSON can publish. No SDK needed.

---

## Subscriber Development

### In-process (Python class)

```python
from hookbus import Subscriber

class SlackAlerts(Subscriber):
    async def on_event(self, event):
        if event.decision == "deny":
            post_to_slack(f"Agent blocked: {event.reason}")
```

Add to config:

```yaml
  - name: slack-alerts
    type: async
    module: subscribers.slack_alerts.SlackAlerts
    events: ["PreToolUse"]
```

### Out-of-process (socket/HTTP)

Subscriber listens on a socket or HTTP endpoint. Bus sends JSON events. Sync subscribers respond with JSON decisions.

### Base class

```python
class Subscriber:
    name: str

    async def on_event(self, event) -> Response | None:
        """Override this. Return Response for sync, None for async."""
        pass

    async def start(self):
        """Called once when bus loads subscriber."""
        pass

    async def stop(self):
        """Called on shutdown."""
        pass
```

---

## CLI

```bash
hookbus start                          # Start the bus
hookbus stop                           # Stop the bus
hookbus status                         # Show bus status + subscriber health

hookbus connect claude-code            # Auto-wire assistant
hookbus connect cursor
hookbus connect augment

hookbus add slack --type async --transport http --address http://localhost:9200
hookbus remove slack
hookbus list                           # List all subscribers
hookbus test cre-gate                  # Send test event, show response

hookbus events                         # Stream live events (tail -f style)
hookbus events --type PreToolUse       # Filter by type
hookbus events --subscriber cre-gate   # Filter by subscriber
```

---

## Potential Subscribers

### Governance (sync, blocking)

| Subscriber | What it does |
|---|---|
| Policy Gate | Apply organisation policy to tool calls. Allow/deny/ask. |
| Budget Gate | Token/cost limit per session, user, or team. Deny if exceeded. |
| Rate Limiter | Too many tool calls per minute. Throttle or deny. |
| Compliance Gate | PCI, SOX, HIPAA rules per industry vertical. |
| Approval Router | Route to manager for sign-off. Async approval flow. |

### Observability (async)

| Subscriber | What it does |
|---|---|
| Audit Logger | Every event to SQLite, S3, or cloud storage. |
| Token Counter | Track cost per agent, per user, per team. Dashboards. |
| Analytics | Tool call frequency, block rate, latency percentiles. |
| SIEM Forward | Splunk, Datadog, Elastic. Security event forwarding. |

### Intelligence (async)

| Subscriber | What it does |
|---|---|
| Learning Engine | Derive new rules from patterns of blocked/allowed events. |
| Memory Writer | Save what the agent did to long-term memory. |
| Context Enricher | Feed agent's history back next session. |
| Anomaly Detector | Agent behaving differently than usual? Alert. |

### Communication (async)

| Subscriber | What it does |
|---|---|
| Slack/Teams Alerts | Notify channel on deny events. |
| Email Digest | Daily summary of agent activity. |
| Push Notification | Real-time alerts to phone (ntfy, PagerDuty). |
| Webhook Forwarder | Forward events to any HTTP endpoint. |

### Agent Coordination (sync or async)

| Subscriber | What it does |
|---|---|
| Agent Registry | Track which agents are running and what they are doing. |
| Conflict Detector | Two agents editing the same file? Block one. |
| Handoff Broker | Agent A passes work to agent B via the bus. |

---

## Repo Structure

```
hookbus/
  hookbus/
    bus.py              # The router
    protocol.py         # Event/response dataclasses
    client.py           # Thin client (what hooks call)
    config.py           # Load subscriber config
    http_server.py      # HTTP endpoint for webhook publishers
    base_subscriber.py  # Template for new subscribers
    hooks/
      openai.py         # Pre-built OpenAI SDK hook
      anthropic.py      # Pre-built Anthropic SDK hook
      langchain.py      # Pre-built LangChain callback
      crewai.py         # Pre-built CrewAI step callback
  subscribers/
    echo.py             # Test subscriber (returns allow for everything)
    audit.py            # Event logger
  hookbus.yaml          # Default subscriber config
  Dockerfile
  pyproject.toml
  README.md
  LICENSE               # Apache 2.0
```

Policy subscribers are shipped as separate packages. The bus and protocol are open source.

---

## Migration from Bridge

| Current (bridge.py) | Moves to |
|---|---|
| Socket server, message routing | bus.py |
| Policy evaluation logic | Policy subscriber (separate package) |
| KB / preprompt handling | Policy subscriber |
| Event logging | Audit subscriber |
| Rules cache, config loading | Policy subscriber internal |

### Phase 1: Bus skeleton
- bus.py with subscriber config and fan-out
- protocol.py with event/response format
- client.py (thin client)
- Test with echo subscriber

### Phase 2: Policy as subscriber
- Extract policy evaluation from bridge.py into a policy subscriber
- Policy subscriber listens on its own socket
- Bus routes PreToolUse to the policy subscriber
- End-to-end test: client -> bus -> policy subscriber -> decision

### Phase 3: Async subscribers
- Audit logger as async subscriber
- Test sync + async running together

### Phase 4: Clean up
- Remove bridge.py from legacy locations
- Update Docker image
- Update thin client
- Update docs

---

## Patent Claims (for US provisional filing)

1. Universal event bus that intercepts AI agent lifecycle events from heterogeneous sources
2. Standardised event format normalised from multiple AI assistant and SDK hook systems
3. Subscriber registry with configurable sync/async routing mode per subscriber
4. Sync fan-out with deny-wins decision consolidation
5. Async fire-and-forget for observability and intelligence subscribers
6. Event-type agnostic routing (new lifecycle events route without code changes)
7. Hot-reload subscriber configuration without bus restart
8. Multiple transport protocols (Unix socket, HTTP, in-process) per subscriber
9. External position between AI assistant process and OS tool execution

---

## Competitive Landscape (as of April 2026)

| Product | What it is | What it lacks vs HookBus |
|---|---|---|
| Microsoft AGT | Single-pipeline policy engine | No bus, no multi-subscriber fan-out, no async |
| AEGIS | Three-stage firewall | No bus, no subscriber model, single pipeline |
| DACP | Governance gateway with MCP proxy | No event fan-out, no async, single policy gate |
| AgentSH | Syscall-level interception | No bus, no subscriber model, wrong abstraction level |
| OWASP ACS | Standard/specification | Defines hook points, not routing architecture |
| LangChain/OpenAI hooks | Framework-specific callbacks | Locked to one SDK, not universal, no fan-out |

Nobody has built the universal bus with multi-subscriber fan-out and sync/async routing for AI agent lifecycle events.
