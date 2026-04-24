# HookBus Subscriber Backlog

Working backlog of subscriber ideas and their architectural tier. Discussed 2026-04-21. Not an exhaustive list, not a promise: a living document for what we think is worth building and why.

## The four subscriber tiers

| Tier | Pattern | Blocking? | Examples today | Key risk |
|---|---|---|---|---|
| 1. Hard gates | Sync subscriber returns `allow`/`deny`/`ask` | ✅ yes | CRE-AgentProtect, DLP Filter | latency on every event |
| 2. Skill-injection | Subscriber returns `allow + context: "load skill X"` | ❌ advisory | (none yet) | LLM can ignore or be prompt-injected |
| 3. Index / Vault | Async, persists state, queryable by other subscribers | ❌ observational | (none yet) | storage growth, content sensitivity |
| 4. Integration (external SaaS) | Async fan-out to webhook / third-party | ❌ observational | (none yet) | data sovereignty, PII leaving the perimeter |

Subscribers in a given tier compose: a bus can host many hard gates + many skill-injection subscribers + many vaults + many integrations, all reading the same events. The tier dictates what a subscriber *can* do, not who it is.

---

## Tier 1: Hard gates (already shipping / in-flight)

- ✅ **CRE-AgentProtect**: Microsoft AGT L1 policy engine. Shipped.
- ✅ **AgentSpend**: token/cost tracking (observational, not gate). Shipped.
- 🟡 **DLP Filter**: regex-based secret/PII redaction at envelope boundary. Enterprise tier, running in a development deployment.
- 🟡 **Auditor**: hash-chained immutable event log for SOC 2 / EU AI Act Art. 12. Enterprise tier, running in a development deployment.
- 🟡 **KB Injector**: context injection on UserPromptSubmit based on keyword match. Enterprise tier, running in a development deployment.
- 🟡 **Session Memory**: cross-turn agent memory correlated by session_id. Enterprise tier, running in a development deployment.

All Enterprise-tier items stay until the bus-side consolidation and licensing story is ready for Light.

---

## Tier 2: Skill-injection subscriber (new pattern)

**Idea:** subscriber reads an agent-native or bus-native skills folder, matches event shape to skill frontmatter, returns a verdict whose `context` field injects a "load skill X" instruction into the agent's prompt. Agent runs the skill in its own process.

**Separation of concerns:**
1. Publisher puts the event on the bus (envelope has `source`, `event_type`, `tool_name`, `tool_input`).
2. Bus dispatches to the skill-injection subscriber.
3. Subscriber walks its skill registry, picks matching skills based on frontmatter.
4. Subscriber returns `allow` + `context: "load skill prod-deploy-guardrails before continuing"`.
5. Publisher surfaces context to the agent via existing `additionalContext` / `message` injection path.
6. Agent loads and runs the skill using its native skill-loading machinery.

**Why this is good:**
- Skills keep their in-process advantages (access to LLM, agent tools, session state).
- Bus stays out of subprocess management.
- Existing skill marketplaces (Anthropic, Amp, community) become HookBus content.

**Why this is limited:**
- **Advisory, not enforcement.** LLM can ignore injected context, especially under prompt injection. Fine for coding-standards and context injection, not fine for `rm -rf /prod`.
- Only works on events whose publisher supports context injection (UserPromptSubmit everywhere; PreToolUse on some publishers; not PostToolUse / Stop).

**Scope decision to lock:**
- Bus-scoped skills live in `~/.hookbus/skills/`: apply to every publisher.
- Agent-local skills stay in `~/.claude/skills/`, `~/.config/amp/plugins/`, `~/.hermes/skills/`: do NOT double as bus rules by default.
- Skill frontmatter field `scope: all | claude-code | amp | hermes | …` lets an author widen or narrow.

**Status:** design-complete, not yet built. Priority: medium. First candidate to validate on agentspend subscriber host.

---

## Tier 3: Index / Vault subscribers (new pattern)

### 3a. Audit Vault / Memory Vault

**Idea:** subscriber watches `create_file`, `edit_file`, `Read`, `Write`, `Bash` events, extracts paths + content deltas, persists them to a queryable index (SQLite for paths, diff snapshots for content, optional vector DB for semantic search).

**What it unlocks:**
- Full agent-data lineage: every file any agent touched, when, why, what changed.
- EU AI Act Article 12 audit evidence: the compliance artefact regulated enterprises need.
- Cross-agent memory: Amp writes `config.py`; Claude Code later reads it and the vault injects "this file was last modified by Amp 3 days ago with reason X".
- Forensics replay: incident happens, full tool-call history is queryable.
- Queryable by other subscribers: DLP can ask "has this path been exfiltrated before?" and get an answer.

**Architectural notes:**
- Async for indexing (don't block agent). Optional sync query path for other subscribers.
- Storage policy: per-session / per-project / tiered retention. Non-trivial.
- Encryption at rest; bearer-only reads. The vault itself becomes a target, treat accordingly.
- Only sees what publishers emit. Human edits outside the agent flow are invisible unless paired with a filesystem watchdog (scope creep: park).

**Strategic read:** turns HookBus from a runtime-governance product into a runtime-*intelligence* product. Nobody else in the space has this angle (Noma, Cupcake, Oasis are all stateless). Enterprise tier.

**Status:** greenfield. Priority: high for Enterprise tier, not for Light.

### 3b. Session-Memory subscriber (existing, Enterprise)

Already on 249. Same tier. Cross-turn correlation by `session_id`. Mentioned here for completeness.

---

## Tier 4: Integration subscribers (new pattern)

### 4a. `hookbus-publisher-webhook`: generic HTTP-out subscriber

**Idea:** YAML-configured generic webhook poster. One subscriber, infinite targets. Config:
- `endpoint_url`
- `headers` (auth, content-type)
- `body_template` (Jinja/Handlebars over envelope)
- `event_filter` (which events to forward)
- `rate_limit` (per-second cap)
- `redact` (list of envelope fields to scrub)

Covers 80% of "send my events to X" requests instantly: Monday.com, Airtable, Zapier, Make.com, custom internal webhooks. Ship this first.

**Status:** primary priority. Ships an entire integration surface for one subscriber's worth of work.

### 4b. Branded dedicated subscribers

For platforms where type-aware adapters beat a generic webhook:

- **Notion**: event rows as database entries, structured property types (select, multi-select, URL, date), session-summary pages on `Stop`.
- **Slack**: rich formatting, threading per `session_id`, alert channels for `deny` events.
- **Linear**: issue creation on specific `deny` patterns, project tagging from envelope metadata.
- **Jira**: same pattern as Linear, for the enterprise crowd.
- **GitHub**: issue/PR comments on matching events, CI pipeline integration.

Each is a small subscriber wrapping the generic webhook shape with platform-specific niceties.

**Strategic read:** Zapier pattern, but for agent runtime events. At 20+ integrations, HookBus becomes the neutral plane that routes agent events to wherever enterprise teams already work. That's a different product story than "governance layer": it's "the audit/observability fabric for agentic workflows."

**Status:** after the generic webhook subscriber ships. Notion + Slack + Linear are the right first three branded ones.

---

## Cross-cutting concerns

- **DLP in front of integration subscribers.** Tool_input and tool_result can contain secrets, PII, proprietary code. Any subscriber that ships envelopes outside the trust boundary needs either the DLP Filter upstream or per-field redaction baked in. Non-negotiable for regulated buyers.

- **Rate limiting per subscriber.** Sync hard gates add latency; integration subscribers can hit third-party API quotas. Each subscriber declares its own rate budget; bus consolidator respects it.

- **Per-subscriber auth.** Every external integration needs its own credential (Notion token, Slack webhook, Linear API key). Keep these out of the bus config; per-subscriber `.env` or secret store.

- **Backpressure.** If a slow subscriber falls behind, events must either queue (async tier) or fail-open/fail-closed per config (sync tier). Bus should not block on async subscribers.

- **Enterprise-tier gating.** Tier 3 (Vault) and most of Tier 4 live in Enterprise. Light ships with only Tier 1 (CRE-AgentProtect, AgentSpend). Document the boundary.

---

## Patterns NOT to build

- Subscribers that MUTATE envelope on the way to other subscribers. Consolidation is a bus-level job, not a subscriber-level one. Keep subscriber contracts pure observer / pure gate.
- Subscribers that initiate events (push model). HookBus is reactive; adding a push path changes the protocol shape. Separate concern: belongs in the publisher layer, not the subscriber layer.
- Per-agent-scoped subscribers. The bus is the cross-agent plane; if a rule only applies to one agent, it belongs in that agent's skill/hook config, not in the bus.

---

## Immediate priority order (as of 2026-04-21)

1. Finish the Light-tier v0.1 push (claude-code / hermes / openclaw / amp v2.1 + per-subscriber namespacing fix (avoids downstream prefix collisions) + bus install.sh update). Not in this backlog: see release coordination.
2. Skill-injection subscriber (Tier 2): small, high-leverage, validates the skills-as-policy pattern.
3. Generic `hookbus-publisher-webhook` (Tier 4a): unlocks the whole integration story.
4. Notion subscriber (Tier 4b): first branded integration, enterprise distribution lever.
5. Audit Vault (Tier 3a): Enterprise tier, compliance artefact story.
