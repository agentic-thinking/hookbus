import json

from hookbus_normaliser import HookBusNormaliser, NormaliserConfig, NormalisedEvent


class FakeNormaliser(HookBusNormaliser):
    def __init__(self, responses, http_payloads=None):
        super().__init__(NormaliserConfig(agentflow_approval_timeout=2, agentflow_poll_interval=0.01))
        self.responses = list(responses)
        self.http_payloads = http_payloads or {}
        self.posted = []

    def post_hookbus(self, envelope):
        self.posted.append(envelope)
        if not self.responses:
            return {"decision": "allow", "reason": "ok"}
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get_json(self, url):
        for key, payload in self.http_payloads.items():
            if key in url:
                return payload
        raise RuntimeError("missing fake payload " + url)


def event(session="s1"):
    return NormalisedEvent(source="claude-code", event_type="PreToolUse", tool_name="Bash", tool_input={"command": "date"}, session_id=session)


def test_allow():
    n = FakeNormaliser([{"decision": "allow", "reason": "ok"}])
    r = n.handle(event())
    assert r.decision == "allow"
    assert r.exit_code == 0


def test_deny():
    n = FakeNormaliser([{"decision": "deny", "reason": "blocked"}])
    r = n.handle(event())
    assert r.decision == "deny"
    assert r.exit_code == 2


def test_ask_approval_rechecks_and_allows():
    action = {"id": 7, "workflow_id": "wf1", "status": "approved", "tool_name": "Bash", "tool_input": json.dumps({"command": "date"}), "session_id": "s1", "source": "claude-code"}
    n = FakeNormaliser([
        {"decision": "ask", "reason": "need approval", "subscriber_responses": [{"subscriber": "AgentFlow", "metadata": {"workflow_id": "wf1", "action_id": 7}}]},
        {"decision": "allow", "reason": "workflow approved"},
    ], {"/api/action/7": {"action": action}})
    r = n.handle(event())
    assert r.decision == "allow"
    assert "workflow approved" in r.reason
    assert len(n.posted) == 2
    assert n.posted[1]["metadata"]["agentflow_decision_check"] is True


def test_ask_rejected_blocks():
    action = {"id": 8, "workflow_id": "wf2", "status": "rejected", "reason": "no", "tool_name": "Bash", "tool_input": json.dumps({"command": "date"}), "session_id": "s1"}
    n = FakeNormaliser([
        {"decision": "ask", "reason": "need approval", "subscriber_responses": [{"subscriber": "AgentFlow", "metadata": {"workflow_id": "wf2", "action_id": 8}}]},
    ], {"/api/action/8": {"action": action}})
    r = n.handle(event())
    assert r.decision == "deny"
    assert r.reason == "no"


def test_ask_can_find_action_by_session_after_it_leaves_pending():
    action = {"id": 9, "workflow_id": "wf3", "status": "approved", "tool_name": "Bash", "tool_input": json.dumps({"command": "date"}), "session_id": "s1", "source": "claude-code"}
    n = FakeNormaliser([
        {"decision": "ask", "reason": "need approval", "subscriber_responses": []},
        {"decision": "allow", "reason": "session action approved"},
    ], {"/api/actions?": [action], "/api/action/9": {"action": action}})
    r = n.handle(event())
    assert r.decision == "allow"
    assert "session action approved" in r.reason
