# HookBus Gate (multi-mode)

One Node.js binary that serves as the publisher shim for **eight** agent CLIs and frameworks. Reads hook JSON on stdin, posts a HookEvent envelope to the bus, translates the verdict back into the CLI's expected output schema.

```
  Claude Code        ─┐
  OpenAI Codex       ─┤
  Google Gemini      ─┤
  Auggie             ─├──►  hookbus-gate  ──►  HookBus  ──►  subscribers
  Sourcegraph Amp    ─┤      (--mode=X)          :18800        (cre, agentspend, ...)
  OpenClaw           ─┤
  Hermes             ─┤
  generic shell      ─┘
```

Install it once per host. Each CLI's hook config invokes the same binary with a different `--mode=<handler>` flag.

## Install

### Binary

Node 20+ required.

```bash
git clone https://github.com/agentic-thinking/hookbus.git
cd hookbus/hookbus/publishers/hookbus-gate
npm install
sudo cp -r . /opt/hookbus-gate
```

The gate now lives at `/opt/hookbus-gate/src/index.js`.

### Shared environment (every CLI reads these)

```bash
export HOOKBUS_URL=http://localhost:18800/event
export HOOKBUS_TOKEN=$(docker exec hookbus cat /root/.hookbus/.token)
```

**Do NOT add `HOOKBUS_SOURCE` to your shell profile.** That will leak into every CLI on the host and mislabel their events. Always pin `HOOKBUS_SOURCE` inline per-CLI (see below).

## Per-CLI wiring

### Claude Code (Anthropic)

Write `~/.claude/settings.json`:

```jsonc
{
  "hooks": {
    "UserPromptSubmit": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "env HOOKBUS_SOURCE=claude-code node /opt/hookbus-gate/src/index.js --mode=claude-code-hook"
      }]
    }],
    "PreToolUse":  [{ "matcher": "", "hooks": [{ "type": "command", "command": "env HOOKBUS_SOURCE=claude-code node /opt/hookbus-gate/src/index.js --mode=claude-code-hook" }] }],
    "PostToolUse": [{ "matcher": "", "hooks": [{ "type": "command", "command": "env HOOKBUS_SOURCE=claude-code node /opt/hookbus-gate/src/index.js --mode=claude-code-hook" }] }],
    "Stop":        [{ "matcher": "", "hooks": [{ "type": "command", "command": "env HOOKBUS_SOURCE=claude-code node /opt/hookbus-gate/src/index.js --mode=claude-code-hook" }] }]
  }
}
```

### OpenAI Codex CLI

Codex hooks are behind an experimental flag. Enable it in `~/.codex/config.toml`:

```toml
[features]
codex_hooks = true
```

Then write `~/.codex/hooks.json`:

```jsonc
{
  "SessionStart":     [{ "command": "env HOOKBUS_SOURCE=codex node /opt/hookbus-gate/src/index.js --mode=codex-hook" }],
  "UserPromptSubmit": [{ "command": "env HOOKBUS_SOURCE=codex node /opt/hookbus-gate/src/index.js --mode=codex-hook" }],
  "PreToolUse":       [{ "command": "env HOOKBUS_SOURCE=codex node /opt/hookbus-gate/src/index.js --mode=codex-hook" }],
  "PostToolUse":      [{ "command": "env HOOKBUS_SOURCE=codex node /opt/hookbus-gate/src/index.js --mode=codex-hook" }],
  "Stop":             [{ "command": "env HOOKBUS_SOURCE=codex node /opt/hookbus-gate/src/index.js --mode=codex-hook" }]
}
```

Codex's `PreToolUse` currently only fires for the `Bash` tool. Read / Write / Edit don't yet surface through hooks. Tracked upstream; your dashboard will see fewer tool events per session than Claude Code until OpenAI widens coverage.

### Google Gemini CLI

Write `~/.gemini/settings.json`:

```jsonc
{
  "hooks": {
    "BeforeAgent": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "env HOOKBUS_SOURCE=gemini-cli node /opt/hookbus-gate/src/index.js --mode=gemini-beforeagent",
        "timeout": 10000
      }]
    }],
    "BeforeTool": [{
      "matcher": "*",
      "hooks": [{
        "type": "command",
        "command": "env HOOKBUS_SOURCE=gemini-cli node /opt/hookbus-gate/src/index.js --mode=claude-code-hook",
        "timeout": 10000
      }]
    }]
  }
}
```

Gemini hooks are enabled by default since v0.26. `BeforeAgent` is the context-injection slot; its handler emits `hookSpecificOutput.additionalContext` containing any KB / policy text returned by HookBus subscribers.

### Auggie

Same protocol as Claude Code. Point Auggie's hook config at `--mode=claude-code-hook` with `HOOKBUS_SOURCE=auggie` so events are labelled correctly.

### OpenClaw, Hermes, Amp

Each has its own handler (`--mode=openclaw-plugin`, `--mode=hermes`, `--mode=amp-delegate`). See those CLIs' README in the corresponding publisher repo for full wiring; the plugin or systemd drop-in pattern is documented per-vendor.

## Handler reference

| Mode | CLI | Event semantics | Exit codes |
|---|---|---|---|
| `claude-code-hook` | Claude Code, Auggie | stdin JSON in, `0` allow / `2` deny (stderr = reason) | 0, 2 |
| `codex-hook` | OpenAI Codex | per-event output (`hookSpecificOutput` for PreToolUse, `systemMessage` for Stop/SessionStart) | 0, 2 |
| `gemini-beforeagent` | Google Gemini | emits `hookSpecificOutput.additionalContext` (KB injection) | 0, 2 |
| `openclaw-plugin` | OpenClaw | WebSocket-bridged plugin protocol | 0, 2 |
| `hermes` | Hermes agent | in-process Python plugin helper | 0, 2 |
| `amp-delegate` | Sourcegraph Amp | delegate-permissions JSON protocol | 0 (allow), 1 (ask), 2 (deny) |
| `shell-wrapper` | generic shell | subshell wrapper for arbitrary commands | 0, 2 |

## Authentication

The bus enforces a bearer token since v0.1. All hooks inherit `HOOKBUS_TOKEN` from the per-command `env` or from the gate process environment. Never commit tokens to the CLI config file directly.

Retrieve the token once per bus:

```bash
docker exec hookbus cat /root/.hookbus/.token
```

## Fail-open behaviour

If the bus is unreachable (container stopped, network down), every handler fails **open**: it allows the action so the CLI is never bricked by a missing bus. For fail-closed behaviour required by regulated environments, see `HOOKBUS_FAIL_MODE=closed` support in individual handlers.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Events show with wrong `source` label | `HOOKBUS_SOURCE` is exported globally in a shell profile; remove and rely on inline `env` per command |
| Claude Code rejects Stop hook output as invalid JSON | Gate is older than the per-event-type schema fix; update to current `claude-code-hook.js` |
| Codex hook never fires | `features.codex_hooks` not enabled in `~/.codex/config.toml` |
| Gemini hook fires but no context injected | Check that `BeforeAgent` uses `--mode=gemini-beforeagent`, not `--mode=claude-code-hook` |
| Bus returns 401 | `HOOKBUS_TOKEN` is stale; re-read from the container |
| Dashboard shows events but blank tool column | Expected for `UserPromptSubmit`, `Stop`, `SessionStart` — those events have no tool by design; the column falls back to event_type in italics |

## Licence

Apache 2.0. See root `LICENSE`.
