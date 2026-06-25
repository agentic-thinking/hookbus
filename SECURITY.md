# Security Policy

HookBus is a public reference implementation for agent lifecycle governance. Treat it as a developer preview unless you have reviewed and pinned the deployment configuration.

## Deployment posture

- Run gating deployments fail-closed. The server entry point defaults `HOOKBUS_FAIL_OPEN=0`; set `HOOKBUS_FAIL_OPEN=1` only for observability-only experiments.
- Use a unique bearer token per deployment and keep it out of command logs, screenshots, and issue reports.
- Bind public deployments behind a reverse proxy with TLS and explicit network allow-lists.
- Do not expose dashboard or subscriber admin endpoints directly to the internet.
- Pin container versions for pilots; avoid floating `latest` in regulated or customer environments.

## Reporting vulnerabilities

Please report security issues privately to Agentic Thinking rather than opening a public issue with exploit details. Include the affected version/commit, configuration, reproduction steps, and expected impact.

## Current hardening focus

The current public hardening work is focused on fail-closed behaviour when sync subscribers fail, hot-reload safety, publisher compatibility, and evidence completeness.
