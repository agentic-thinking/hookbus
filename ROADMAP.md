# HookBus Public Roadmap

This roadmap is intentionally narrow. HookBus public stays as the clean open-source wedge: the bus, protocol, publisher shims, and light governance subscribers. Enterprise/private subscribers remain separate.

## Now

- Fail-closed bus semantics for missing or crashed sync subscribers.
- Safer hot reload: preserve existing subscribers if config reload fails.
- Publisher compatibility for Claude Code, Codex, Amp, OpenCode, Hermes, and OpenClaw style runtimes.
- Clear install and doctor flows for local developer evaluation.

## Next

- More regression tests around subscriber failure, timeout, and consolidation behaviour.
- Better evidence surfaces for what subscriber responded, what failed, and why a decision was reached.
- Cleaner shared primitives for auth, event normalization, and subscriber response handling.
- Documentation that separates dev-preview defaults from hardened pilot deployments.

## Later

- Formal AgentHook conformance fixtures.
- Production deployment guide with reverse proxy, token rotation, and observability patterns.
- More publisher examples and compatibility notes.
