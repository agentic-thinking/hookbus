"""
HookBus - Universal Event Bus for AI Agent Lifecycle Enforcement

Version: 1.0
"""

from .protocol import (
    HookEvent,
    SubscriberResponse,
    EventType,
    Decision,
    SubscriberType,
    Transport,
    DateTimeEncoder,
    consolidate_decisions
)
from .client import HookBusClient, create_client, publish
from .bus import Bus, SubscriberConfig

__version__ = "1.0"
__all__ = [
    "HookEvent",
    "SubscriberResponse",
    "EventType",
    "Decision",
    "SubscriberType",
    "Transport",
    "DateTimeEncoder",
    "consolidate_decisions",
    "HookBusClient",
    "create_client",
    "publish",
    "Bus",
    "SubscriberConfig",
]
