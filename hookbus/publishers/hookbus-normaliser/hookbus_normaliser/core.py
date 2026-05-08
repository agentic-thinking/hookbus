"""Shared HookBus publisher normalisation core.

This module intentionally contains no vendor-runtime formatting. It accepts a
neutral NormalisedEvent and returns a NormalisedResult that adapters can format
for Claude Code, Codex, Amp, Hermes, OpenClaw, OpenCode, or future runtimes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
import time
import uuid
import urllib.parse
import urllib.request
from typing import Any, Callable


@dataclass(slots=True)
class NormaliserConfig:
    hookbus_url: str = "http://localhost:18800/event"
    hookbus_token: str = ""
    hookbus_timeout: int = 30
    fail_mode: str = "open"
    agentflow_url: str = "http://localhost:8893"
    agentflow_approval_timeout: int = 900
    agentflow_poll_interval: float = 2.0
    approval_actor: str = "email-approval"
    publisher_name: str = "hookbus-normaliser"
    publisher_version: str = "0.1.0"

    @classmethod
    def from_env(cls) -> "NormaliserConfig":
        return cls(
            hookbus_url=os.environ.get("HOOKBUS_URL", "http://localhost:18800/event"),
            hookbus_token=os.environ.get("HOOKBUS_TOKEN", "").strip(),
            hookbus_timeout=int(os.environ.get("HOOKBUS_TIMEOUT", "30")),
            fail_mode="closed" if os.environ.get("HOOKBUS_FAIL_MODE", "open").lower() == "closed" else "open",
            agentflow_url=os.environ.get("AGENTFLOW_URL", "http://localhost:8893").rstrip("/"),
            agentflow_approval_timeout=int(os.environ.get("AGENTFLOW_APPROVAL_TIMEOUT", "900")),
            agentflow_poll_interval=float(os.environ.get("AGENTFLOW_POLL_INTERVAL", "2")),
            approval_actor=os.environ.get("AGENTFLOW_APPROVAL_ACTOR", "email-approval"),
            publisher_name=os.environ.get("HOOKBUS_NORMALISER_NAME", "hookbus-normaliser"),
            publisher_version=os.environ.get("HOOKBUS_NORMALISER_VERSION", "0.1.0"),
        )


@dataclass(slots=True)
class NormalisedEvent:
    source: str
    event_type: str
    tool_name: str = ""
    tool_input: Any = field(default_factory=dict)
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    event_id: str = ""
    timestamp: str = ""


@dataclass(slots=True)
class NormalisedResult:
    decision: str
    reason: str = ""
    preprompt: str = ""
    additional_context: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    exit_code: int = 0

    @property
    def context(self) -> str:
        return "\n\n".join(part for part in (self.preprompt, self.additional_context or self.reason) if part)


class HookBusNormaliser:
    """AgentHook-compatible normalisation and approval-control core."""

    def __init__(self, config: NormaliserConfig | None = None, http_json: Callable[..., Any] | None = None):
        self.config = config or NormaliserConfig.from_env()
        self._http_json_override = http_json

    def envelope(self, event: NormalisedEvent) -> dict[str, Any]:
        metadata = dict(event.metadata or {})
        metadata.setdefault("publisher", self.config.publisher_name)
        metadata.setdefault("publisher_version", self.config.publisher_version)
        return {
            "event_id": event.event_id or str(uuid.uuid4()),
            "event_type": event.event_type,
            "timestamp": event.timestamp or datetime.now(timezone.utc).isoformat(),
            "source": event.source,
            "session_id": event.session_id,
            "tool_name": event.tool_name,
            "tool_input": event.tool_input if isinstance(event.tool_input, dict) else {"value": event.tool_input},
            "metadata": metadata,
        }

    def handle(self, event: NormalisedEvent) -> NormalisedResult:
        envelope = self.envelope(event)
        try:
            result = self.post_hookbus(envelope)
        except Exception as exc:
            reason = f"HookBus unreachable: {exc}"
            if self.config.fail_mode == "closed" and event.event_type == "PreToolUse":
                return NormalisedResult("deny", reason=reason, raw={}, exit_code=2)
            return NormalisedResult("allow", raw={}, exit_code=0)

        decision = (result.get("decision") or "allow").lower()
        reason = result.get("reason") or ""
        preprompt = result.get("preprompt") or result.get("additional_context") or ""

        if decision == "ask" and event.event_type == "PreToolUse":
            return self.wait_for_agentflow(envelope, result, reason)
        if decision == "deny":
            return NormalisedResult("deny", reason=reason, preprompt=preprompt, raw=result, exit_code=2)
        return NormalisedResult("allow", reason=reason, preprompt=preprompt, raw=result, exit_code=0)

    def post_hookbus(self, envelope: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(envelope).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.config.hookbus_token:
            headers["Authorization"] = "Bearer " + self.config.hookbus_token
        req = urllib.request.Request(self.config.hookbus_url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=self.config.hookbus_timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_json(self, url: str) -> Any:
        if self._http_json_override:
            return self._http_json_override(url)
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=self.config.hookbus_timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def wait_for_agentflow(self, envelope: dict[str, Any], bus_result: dict[str, Any], original_reason: str) -> NormalisedResult:
        row = self.agentflow_action_from_bus_result(bus_result)
        if not row:
            deadline = time.time() + min(10, max(1, self.config.agentflow_approval_timeout))
            while time.time() < deadline:
                row = self.find_agentflow_action_by_session(envelope) or self.find_pending_agentflow_action(envelope)
                if row:
                    break
                time.sleep(min(self.config.agentflow_poll_interval, 1))
        if not row:
            return NormalisedResult(
                "deny",
                reason=original_reason + "\n\nAgentFlow approval required, but no matching approval action could be found.",
                raw=bus_result,
                exit_code=2,
            )

        action_id = row.get("id") or row.get("action_id")
        workflow_id = row.get("workflow_id")
        approve_url = self.approval_url(action_id, workflow_id)
        deadline = time.time() + self.config.agentflow_approval_timeout
        while time.time() < deadline:
            current = self.fetch_agentflow_action(action_id)
            action = current.get("action") if isinstance(current, dict) else None
            if isinstance(action, dict):
                status = action.get("status")
                if status == "approved":
                    return self.recheck_approved_workflow(envelope, workflow_id, action_id)
                if status == "rejected":
                    return NormalisedResult(
                        "deny",
                        reason=action.get("reason") or f"AgentFlow rejected workflow {workflow_id} action {action_id}.",
                        raw=current,
                        exit_code=2,
                    )
            time.sleep(self.config.agentflow_poll_interval)

        reason = original_reason + "\n\nAgentFlow approval timed out. The command was not executed."
        if approve_url:
            reason += "\nApproval URL: " + approve_url
        return NormalisedResult("deny", reason=reason, raw=bus_result, exit_code=2)

    def agentflow_action_from_bus_result(self, result: dict[str, Any]) -> dict[str, Any] | None:
        for item in result.get("subscriber_responses") or []:
            if not isinstance(item, dict) or item.get("subscriber") != "AgentFlow":
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            action_id = metadata.get("action_id")
            workflow_id = metadata.get("workflow_id")
            if action_id and workflow_id:
                return {"id": action_id, "action_id": action_id, "workflow_id": workflow_id}
        return None

    def approval_url(self, action_id: Any, workflow_id: Any) -> str:
        if not action_id or not workflow_id:
            return ""
        return self.config.agentflow_url + "/approve?" + urllib.parse.urlencode({
            "workflow_id": workflow_id,
            "action_id": action_id,
            "token": "0000",
            "actor": self.config.approval_actor,
        })

    def fetch_agentflow_action(self, action_id: Any) -> dict[str, Any] | None:
        try:
            return self.get_json(f"{self.config.agentflow_url}/api/action/{int(action_id)}")
        except Exception:
            return None

    def find_agentflow_action_by_session(self, envelope: dict[str, Any]) -> dict[str, Any] | None:
        session_id = envelope.get("session_id") or ""
        if not session_id:
            return None
        try:
            actions = self.get_json(self.config.agentflow_url + "/api/actions?" + urllib.parse.urlencode({"session_id": session_id}))
        except Exception:
            return None
        return self._best_matching_action(actions, envelope, require_session=True)

    def find_pending_agentflow_action(self, envelope: dict[str, Any]) -> dict[str, Any] | None:
        try:
            pending = self.get_json(self.config.agentflow_url + "/api/pending")
        except Exception:
            return None
        return self._best_matching_action(pending, envelope, require_session=bool(envelope.get("session_id")))

    def _best_matching_action(self, rows: Any, envelope: dict[str, Any], require_session: bool = False) -> dict[str, Any] | None:
        if not isinstance(rows, list):
            return None
        tool_name = envelope.get("tool_name")
        tool_input = envelope.get("tool_input")
        session_id = envelope.get("session_id")
        source = envelope.get("source")
        matches = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("tool_name") != tool_name:
                continue
            if not same_json(row.get("tool_input"), tool_input):
                continue
            if require_session and row.get("session_id") != session_id:
                continue
            score = 0
            if row.get("session_id") == session_id:
                score += 4
            if row.get("source") == source:
                score += 2
            try:
                row_id = int(row.get("id") or 0)
            except Exception:
                row_id = 0
            matches.append((score, row_id, row))
        if not matches:
            return None
        matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return matches[0][2]

    def recheck_approved_workflow(self, envelope: dict[str, Any], workflow_id: Any, action_id: Any) -> NormalisedResult:
        retry = json.loads(json.dumps(envelope))
        retry["event_id"] = str(uuid.uuid4())
        metadata = retry.setdefault("metadata", {})
        metadata["workflow_id"] = workflow_id
        metadata["action_id"] = action_id
        metadata["agentflow_decision_check"] = True
        metadata["original_event_id"] = envelope.get("event_id")
        try:
            result = self.post_hookbus(retry)
        except Exception as exc:
            return NormalisedResult("deny", reason=f"HookBus/CRE approval recheck failed: {exc}", raw={}, exit_code=2)
        decision = (result.get("decision") or "deny").lower()
        reason = result.get("reason") or ""
        preprompt = result.get("preprompt") or result.get("additional_context") or ""
        exit_code = 0 if decision == "allow" else 2
        return NormalisedResult(decision, reason=reason, preprompt=preprompt, raw=result, exit_code=exit_code)


def normalise_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def same_json(left: Any, right: Any) -> bool:
    return normalise_json(left) == normalise_json(right)
