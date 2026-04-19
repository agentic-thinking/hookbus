"""
hookbus_gate, Python sibling of hookbus-gate for agents that live in Python
(OpenAI Agents SDK, LangChain, custom Python agents).

Primary use: wrap tool dispatch with gate_tool_call() before execution.

Example (OpenAI Agents SDK):
    from hookbus_gate import gate_tool_call, HookBusDenied
    from agents import Runner

    # Wrap the Runner so every tool call is gated.
    original_call_tool = Runner._call_tool
    async def gated_call_tool(self, tool, args, ctx):
        await gate_tool_call(source="openai-sdk", tool_name=tool.name,
                             tool_input=args, session_id=ctx.session_id)
        return await original_call_tool(self, tool, args, ctx)
    Runner._call_tool = gated_call_tool

On deny: raises HookBusDenied with reason; caller should surface to the agent.
On bus unreachable / timeout: fail-closed (raises).
"""
import json
import os
import socket
import urllib.request
import urllib.error
import uuid
from datetime import datetime, timezone

BUS_URL = os.environ.get("HOOKBUS_URL", "http://localhost:18800/event")
TOKEN = os.environ.get("HOOKBUS_TOKEN", "")
TIMEOUT_S = int(os.environ.get("HOOKBUS_TIMEOUT_MS", "60000")) / 1000.0


class HookBusDenied(Exception):
    def __init__(self, decision: str, reason: str):
        self.decision = decision
        self.reason = reason
        super().__init__(f"HookBus {decision}: {reason}")


def _build_envelope(source, tool_name, tool_input, session_id=None, event_type="PreToolUse"):
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "session_id": session_id or f"{source}-{socket.gethostname()}-{os.getpid()}",
        "tool_name": tool_name or "unknown",
        "tool_input": tool_input if isinstance(tool_input, dict) else {"value": tool_input},
        "metadata": {"publisher": "hookbus-gate-py", "host": socket.gethostname()},
    }


def _post(envelope):
    body = json.dumps(envelope).encode("utf-8")
    req = urllib.request.Request(
        BUS_URL, data=body, method="POST",
        headers={**{"Content-Type": "application/json"}, **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("decision", "deny"), data.get("reason", "")
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return "deny", f"HookBus unreachable: {e} (fail-closed)"
    except (ValueError, json.JSONDecodeError) as e:
        return "deny", f"HookBus non-JSON response: {e} (fail-closed)"


async def gate_tool_call(source, tool_name, tool_input, session_id=None):
    """Async wrapper. Raises HookBusDenied if decision != 'allow'."""
    envelope = _build_envelope(source, tool_name, tool_input, session_id)
    decision, reason = _post(envelope)
    if decision != "allow":
        raise HookBusDenied(decision, reason or "no reason given")


def gate_tool_call_sync(source, tool_name, tool_input, session_id=None):
    """Synchronous variant for non-async callers."""
    envelope = _build_envelope(source, tool_name, tool_input, session_id)
    decision, reason = _post(envelope)
    if decision != "allow":
        raise HookBusDenied(decision, reason or "no reason given")
