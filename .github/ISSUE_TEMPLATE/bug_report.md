---
name: Bug report
about: Something behaves differently from what HOOKBUS_SPEC.md or CONTRIBUTING.md says
title: "[bug] "
labels: bug
assignees: ''
---

## What happened
<!-- Concrete observed behaviour. Include version/commit if known. -->

## What you expected
<!-- What HOOKBUS_SPEC.md, CONTRIBUTING.md, or README says should happen. -->

## How to reproduce
<!-- Minimal steps. A failing test in tests/ is the gold standard. -->

```
# commands / config / event payload that triggers the bug
```

## Environment
- HookBus version / commit:
- Deployment topology: <!-- single-host / Kubernetes / air-gap -->
- OS + Python version:
- Subscribers active: <!-- list, e.g. cre-light, dlp-filter -->

## Logs / event excerpts
<!-- Redact any secrets or PII. The DLP Filter subscriber catches some patterns; do a manual check too. -->

```
```

## Severity (your judgement)
- [ ] Crash / data loss / event drop
- [ ] Wrong decision returned to publisher
- [ ] Silent miss / cosmetic
