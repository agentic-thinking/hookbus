"""
HookBus Protocol - Event and Response dataclasses for the universal event bus.

This module defines the core data structures used for event publishing,
subscriber responses, and decision consolidation.

Version: 1.0
"""

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Any


class EventType(str, Enum):
    """Known lifecycle event types for AI agent operations."""
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    MODEL_RESPONSE = "ModelResponse"
    AGENT_HANDOFF = "AgentHandoff"
    ERROR_OCCURRED = "ErrorOccurred"


class Decision(str, Enum):
    """Decisions returned by sync subscribers."""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class SubscriberType(str, Enum):
    """Types of subscribers based on response behavior."""
    SYNC = "sync"
    ASYNC = "async"


class Transport(str, Enum):
    """Transport mechanisms for subscriber communication."""
    UNIX_SOCKET = "unix_socket"
    HTTP = "http"
    IN_PROCESS = "in_process"


@dataclass
class HookEvent:
    """
    A lifecycle event from an AI agent or SDK.
    
    This is the core message format that flows through the bus.
    All fields are required unless marked optional.
    
    Example JSON:
    {
        "event_id": "550e8400-e29b-41d4-a716-446655440000",
        "event_type": "PreToolUse",
        "timestamp": "2026-04-08T04:30:00.000Z",
        "source": "claude-code",
        "session_id": "abc123",
        "tool_name": "Bash",
        "tool_input": {"command": "git push --force origin main"},
        "metadata": {}
    }
    """
    event_id: str
    event_type: str
    timestamp: str
    source: str
    session_id: str
    tool_name: str
    tool_input: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize event to JSON string matching spec format."""
        return json.dumps(self.to_dict(), cls=DateTimeEncoder)

    def to_dict(self) -> dict:
        """Convert event to dictionary."""
        return asdict(self)

    @classmethod
    def from_json(cls, json_str: str) -> "HookEvent":
        """Deserialize event from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "HookEvent":
        """Create event from dictionary."""
        return cls(
            event_id=data["event_id"],
            event_type=data["event_type"],
            timestamp=data["timestamp"],
            source=data["source"],
            session_id=data["session_id"],
            tool_name=data.get("tool_name", ""),
            tool_input=data.get("tool_input", {}),
            metadata=data.get("metadata", {})
        )

    @classmethod
    def create(
        cls,
        event_type: str,
        source: str,
        session_id: str,
        tool_name: str,
        tool_input: Optional[dict] = None,
        metadata: Optional[dict] = None
    ) -> "HookEvent":
        """Factory method to create a new event with auto-generated ID and timestamp."""
        return cls(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            source=source,
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input or {},
            metadata=metadata or {}
        )


@dataclass
class SubscriberResponse:
    """
    Response from a sync subscriber to a HookEvent.
    
    Example JSON:
    {
        "event_id": "550e8400-e29b-41d4-a716-446655440000",
        "subscriber": "cre-gate",
        "decision": "deny",
        "reason": "Force push blocked by enterprise policy",
        "metadata": {}
    }
    """
    event_id: str
    subscriber: str
    decision: str
    reason: str = ""
    metadata: dict = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize response to JSON string."""
        return json.dumps(self.to_dict(), cls=DateTimeEncoder)

    def to_dict(self) -> dict:
        """Convert response to dictionary."""
        return asdict(self)

    @classmethod
    def from_json(cls, json_str: str) -> "SubscriberResponse":
        """Deserialize response from JSON string."""
        data = json.loads(json_str)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "SubscriberResponse":
        """Create response from dictionary."""
        return cls(
            event_id=data["event_id"],
            subscriber=data["subscriber"],
            decision=data["decision"],
            reason=data.get("reason", ""),
            metadata=data.get("metadata", {})
        )

    def get_decision(self) -> Decision:
        """Get decision as enum value."""
        return Decision(self.decision)


class DateTimeEncoder(json.JSONEncoder):
    """
    Custom JSON encoder that handles datetime and UUID types.
    
    Converts datetime objects to ISO8601 strings and UUID objects
    to their string representation.
    """
    
    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        if isinstance(obj, uuid.UUID):
            return str(obj)
        return super().default(obj)


def consolidate_decisions(responses: list[SubscriberResponse]) -> tuple[Decision, str]:
    """
    Consolidate multiple subscriber responses using deny-wins logic.
    
    Decision priority (highest to lowest):
    1. DENY - any subscriber says deny, the whole operation is denied
    2. ASK - at least one says ask (and none say deny)
    3. ALLOW - all subscribers allow
    
    Args:
        responses: List of subscriber responses
        
    Returns:
        Tuple of (consolidated decision, combined reason)
    """
    if not responses:
        return Decision.ALLOW, "No subscribers responded"
    
    reasons = []
    has_deny = False
    has_ask = False
    has_allow = False
    
    for response in responses:
        decision = response.get_decision()
        if decision == Decision.DENY:
            has_deny = True
            if response.reason:
                reasons.append(f"[{response.subscriber}] {response.reason}")
        elif decision == Decision.ASK:
            has_ask = True
            if response.reason:
                reasons.append(f"[{response.subscriber}] {response.reason}")
        else:
            has_allow = True
            if response.reason:
                reasons.append(f"[{response.subscriber}] {response.reason}")
    
    combined_reason = "; ".join(reasons) if reasons else ""
    
    if has_deny:
        return Decision.DENY, combined_reason
    if has_ask:
        return Decision.ASK, combined_reason
    return Decision.ALLOW, combined_reason
