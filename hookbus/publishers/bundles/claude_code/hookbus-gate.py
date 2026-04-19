#!/usr/bin/env python3
"""HookBus thin client for Claude Code hooks.

Reads the hook JSON Claude Code writes to stdin, posts it to the HookBus HTTP
endpoint, and translates the bus verdict back into Claude Code's per-event
output schema.

Supported hook events:
    UserPromptSubmit  , plain text injection (additionalContext)
    PreToolUse        , hookSpecificOutput.permissionDecision + reason
    PostToolUse       , hookSpecificOutput.additionalContext (optional)
    Stop              , top-level decision / reason (schema forbids hookSpecificOutput)

Environment:
    HOOKBUS_URL     default http://localhost:18800/event
    HOOKBUS_TOKEN   bearer token, optional
    HOOKBUS_SOURCE  label for dashboard, default claude-code
    HOOKBUS_TIMEOUT HTTP timeout in seconds, default 30

Exit codes:
    0 allow (Claude Code continues)
    2 deny  (Claude Code blocks the action)

On any error (bus unreachable, malformed input) the gate fails open: the hook
allows the action so Claude Code is never bricked by a missing bus.
"""
import json
import os
import sys
import uuid
import urllib.request
from datetime import datetime, timezone


BUS_URL = os.environ.get("HOOKBUS_URL", "http://localhost:18800/event")
TOKEN = os.environ.get("HOOKBUS_TOKEN", "").strip()
SOURCE = os.environ.get("HOOKBUS_SOURCE", "claude-code")
TIMEOUT = int(os.environ.get("HOOKBUS_TIMEOUT", "30"))


def _read_hook_input():
    raw = sys.stdin.read()
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _post_event(envelope):
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = urllib.request.Request(BUS_URL, data=json.dumps(envelope).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def main():
    hook_input = _read_hook_input()
    if hook_input is None:
        sys.exit(0)

    hook = hook_input.get("hook_event_name", "")
    tool_name = hook_input.get("tool_name", hook_input.get("toolName", ""))
    tool_input = hook_input.get("tool_input", hook_input.get("toolInput", {}))

    if not hook:
        hook = "PreToolUse" if tool_name else "UserPromptSubmit"

    if hook == "UserPromptSubmit" and not tool_input:
        tool_input = {"prompt": hook_input.get("prompt", hook_input.get("content", ""))}

    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_type": hook,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": SOURCE,
        "session_id": hook_input.get("session_id", ""),
        "tool_name": tool_name,
        "tool_input": tool_input,
        "metadata": {},
    }

    try:
        result = _post_event(envelope)
    except Exception:
        # Bus unreachable, fail open so Claude Code is never bricked.
        if hook == "UserPromptSubmit":
            sys.exit(0)
        print(json.dumps({}))
        sys.exit(0)

    decision = result.get("decision", "allow")
    reason = result.get("reason", "")

    # UserPromptSubmit: legacy plain-text stdout is the injection path.
    if hook == "UserPromptSubmit":
        if reason.startswith("[cre] "):
            reason = reason[6:]
        sys.stdout.write(reason)
        sys.stdout.flush()
        sys.exit(0)

    # Deny, event-type specific output
    if decision == "deny":
        if hook == "PreToolUse":
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }))
        elif hook == "Stop":
            # Stop schema forbids hookSpecificOutput; use top-level decision.
            print(json.dumps({"decision": "block", "reason": reason}))
        else:
            # PostToolUse and others: no structured deny; surface as systemMessage.
            print(json.dumps({"systemMessage": reason}))
        sys.exit(2)

    # Ask is PreToolUse-only
    if decision == "ask":
        if hook == "PreToolUse":
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "additionalContext": reason,
                }
            }))
        else:
            print(json.dumps({}))
        sys.exit(0)

    # Allow, event-type specific output
    if hook in ("PreToolUse", "PostToolUse"):
        if reason:
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": hook,
                    "additionalContext": reason,
                }
            }))
        else:
            print(json.dumps({}))
    elif hook == "Stop":
        # Stop schema forbids hookSpecificOutput. Silent allow.
        print(json.dumps({}))
    else:
        print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    main()
