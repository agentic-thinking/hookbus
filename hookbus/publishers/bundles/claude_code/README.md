# Claude Code HookBus publisher

`hookbus-gate.py` is a stdin → HTTP shim registered as a Claude Code hook in
`~/.claude/settings.json`. It reads the hook JSON from stdin, POSTs a HookEvent
envelope to HookBus, and returns a decision in Claude Code's per-event-type
output schema.

Supported Claude Code hook events: `UserPromptSubmit`, `PreToolUse`,
`PostToolUse`, `Stop`.

## Install

Copy the file to a runtime path, make it executable:

```bash
install -Dm755 hookbus-gate.py ~/.local/bin/hookbus-gate
```

## Configure (recommended, inline env per hook)

Always pin `HOOKBUS_*` environment variables **inline in each hook command**,
never as a global shell `export`. A global export leaks into every other
HookBus publisher you run on the same box (Hermes, OpenClaw, Amp, your
own scripts), which will cause events from those publishers to be mislabelled
with the wrong `source`.

The correct `settings.json` shape:

```jsonc
{
  "hooks": {
    "UserPromptSubmit": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "env HOOKBUS_URL=http://localhost:18800/event HOOKBUS_TOKEN=your-token HOOKBUS_SOURCE=claude-code /home/you/.local/bin/hookbus-gate"
      }]
    }],
    "PreToolUse":  [/* same inline env */],
    "PostToolUse": [/* same inline env */],
    "Stop":        [/* same inline env */]
  }
}
```

## Do NOT

- Put `export HOOKBUS_SOURCE=claude-code` in `~/.bashrc`, `~/.zshrc`, or any
  login-shell profile. If you do, every other publisher running on this host
  inherits the wrong source label.
- Set `HOOKBUS_*` variables in a top-level `env` block inside `settings.json`.
  Those do not propagate to hook subprocesses on current Claude Code builds.
- Share a single `HOOKBUS_SOURCE` value between multiple publishers. Each
  publisher needs its own label (`claude-code`, `hermes-agent`, `openclaw`,
  `amp`, etc.) so the dashboard can tell them apart.

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `HOOKBUS_URL` | `http://localhost:18800/event` | Bus HTTP endpoint |
| `HOOKBUS_TOKEN` | _(empty)_ | Bearer token, read once from `docker exec hookbus cat /root/.hookbus/.token` |
| `HOOKBUS_SOURCE` | `claude-code` | Source label shown on the dashboard |
| `HOOKBUS_TIMEOUT` | `30` | HTTP timeout (seconds) |

## Failure behaviour

If the bus is unreachable the gate fails open so Claude Code is never bricked
by a missing bus. If you want to fail closed instead, replace the
`urlopen` try/except block with a blocking exit.
