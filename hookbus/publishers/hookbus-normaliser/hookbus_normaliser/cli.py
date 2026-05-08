"""CLI entrypoint for HookBus Normaliser.

Reads a neutral NormalisedEvent JSON object from stdin and writes a
NormalisedResult JSON object to stdout. This lets JavaScript or shell-based
vendor adapters reuse the Python normaliser core without duplicating AgentFlow
approval handling.
"""

from __future__ import annotations

import json
import sys

from .core import HookBusNormaliser, NormalisedEvent


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"decision": "deny", "reason": f"invalid normaliser input: {exc}", "exit_code": 2}))
        return 2

    event = NormalisedEvent(
        source=payload.get("source", "unknown"),
        event_type=payload.get("event_type") or payload.get("hook") or "PreToolUse",
        tool_name=payload.get("tool_name", ""),
        tool_input=payload.get("tool_input", {}),
        session_id=payload.get("session_id", ""),
        metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {},
        event_id=payload.get("event_id", ""),
        timestamp=payload.get("timestamp", ""),
    )
    result = HookBusNormaliser().handle(event)
    print(json.dumps({
        "decision": result.decision,
        "reason": result.reason,
        "preprompt": result.preprompt,
        "additional_context": result.additional_context,
        "context": result.context,
        "exit_code": result.exit_code,
        "raw": result.raw,
    }, ensure_ascii=False))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
