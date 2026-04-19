"""
HookBus Thin Client - Simple interface for publishing events to the bus.

This client normalizes events from various AI SDKs and publishers
before sending them to the bus.

Version: 1.0
"""

import asyncio
import logging
import os
from typing import Optional
from contextlib import asynccontextmanager

import aiohttp

from .protocol import HookEvent


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Normalization map from common SDK hook names to HookBus event types
# Per spec: Normalisation happens in the thin client, never in the bus.
NORMALIZATION_MAP = {
    # OpenAI SDK
    "on_tool_start": "PreToolUse",
    "on_tool_end": "PostToolUse",
    "on_llm_new_token": "ModelResponse",
    
    # Anthropic SDK
    "tool_use_callback": "PreToolUse",
    
    # LangChain
    "on_tool_start": "PreToolUse",
    "on_tool_end": "PostToolUse",
    "on_chain_start": "AgentHandoff",
    "on_chain_end": "AgentHandoff",
    
    # CrewAI
    "step_callback": "PreToolUse",
    
    # Claude Code (already normalized)
    "PreToolUse": "PreToolUse",
    "PostToolUse": "PostToolUse",
    "UserPromptSubmit": "UserPromptSubmit",
    "SessionStart": "SessionStart",
    "SessionEnd": "SessionEnd",
    "ModelResponse": "ModelResponse",
    "AgentHandoff": "AgentHandoff",
    "ErrorOccurred": "ErrorOccurred",
}


def normalize_event_type(raw_event_type: str) -> str:
    """
    Normalize an event type from various SDK formats to HookBus standard.
    
    Args:
        raw_event_type: The raw event type from an SDK or assistant
        
    Returns:
        Normalized event type string
        
    Example:
        >>> normalize_event_type("on_tool_start")
        'PreToolUse'
    """
    return NORMALIZATION_MAP.get(raw_event_type, raw_event_type)


class HookBusClient:
    """
    Thin client for publishing events to the HookBus.
    
    This client is what AI assistants and SDKs call to send events.
    It normalizes the event format before sending to the bus.
    
    Example usage:
        async with HookBusClient() as client:
            await client.publish(
                event_type="PreToolUse",
                tool_name="Bash",
                tool_input={"command": "ls -la"}
            )
    """

    def __init__(
        self,
        bus_address: str = "http://localhost:18800/event",
        source: str = "hookbus-client",
        timeout: float = 30.0
    ):
        """
        Initialize the client.
        
        Args:
            bus_address: HTTP address of the bus endpoint
            source: Identifier for this publisher (e.g., "claude-code", "openai-sdk")
            timeout: Request timeout in seconds
        """
        self.bus_address = bus_address
        self.source = source
        self.timeout = timeout
        # Bearer token for authenticated bus requests. Picked up from env
        # at client instantiation. Empty string = no auth header sent
        # (matches the bus middleware's 503 "server misconfigured" path).
        self._token = os.environ.get("HOOKBUS_TOKEN", "").strip()
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "HookBusClient":
        """Async context manager entry."""
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        if self._session:
            await self._session.close()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure we have an active session."""
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def publish(
        self,
        event_type: str,
        tool_name: str,
        tool_input: Optional[dict] = None,
        session_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        normalize: bool = True
    ) -> dict:
        """
        Publish an event to the bus.
        
        Args:
            event_type: Type of lifecycle event (e.g., "PreToolUse")
            tool_name: Name of the tool being used
            tool_input: Input arguments to the tool
            session_id: Optional session identifier
            metadata: Additional event metadata
            normalize: If True, normalize event_type using the map
            
        Returns:
            Dictionary with decision and reason from the bus
            
        Example:
            >>> async with HookBusClient() as client:
            ...     result = await client.publish(
            ...         event_type="PreToolUse",
            ...         tool_name="Bash",
            ...         tool_input={"command": "echo hello"}
            ...     )
            ...     print(result)
            {'decision': 'allow', 'reason': ''}
        """
        # Normalize event type if requested
        if normalize:
            event_type = normalize_event_type(event_type)
        
        # Generate session ID if not provided
        if not session_id:
            session_id = f"session-{id(asyncio.current_task())}"
        
        # Create event
        event = HookEvent.create(
            event_type=event_type,
            source=self.source,
            session_id=session_id,
            tool_name=tool_name,
            tool_input=tool_input or {},
            metadata=metadata or {}
        )
        
        # Send to bus
        session = await self._ensure_session()
        
        try:
            headers = {"Content-Type": "application/json"}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"
            async with session.post(
                self.bus_address,
                json=event.to_dict(),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                result = await response.json()
                logger.debug(
                    f"Published event {event.event_id}, "
                    f"decision: {result.get('decision')}"
                )
                return result
                
        except asyncio.TimeoutError:
            logger.error(f"Timeout publishing to bus at {self.bus_address}")
            return {
                "event_id": event.event_id,
                "decision": "deny",
                "reason": "Client timeout"
            }
        except Exception as e:
            logger.exception("Error publishing to bus")
            return {
                "event_id": event.event_id,
                "decision": "deny",
                "reason": "Client error"
            }

    async def publish_sync(
        self,
        event_type: str,
        tool_name: str,
        tool_input: Optional[dict] = None,
        session_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        normalize: bool = True
    ) -> tuple[str, str]:
        """
        Publish an event and return just the decision and reason.
        
        Args:
            Same as publish()
            
        Returns:
            Tuple of (decision, reason)
        """
        result = await self.publish(
            event_type=event_type,
            tool_name=tool_name,
            tool_input=tool_input,
            session_id=session_id,
            metadata=metadata,
            normalize=normalize
        )
        return result.get("decision", "deny"), result.get("reason", "")

    async def close(self) -> None:
        """Close the client session."""
        if self._session:
            await self._session.close()
            self._session = None


@asynccontextmanager
async def create_client(
    bus_address: str = "http://localhost:18800/event",
    source: str = "hookbus-client"
):
    """
    Async context manager for creating a HookBus client.
    
    Example:
        async with create_client(source="my-agent") as client:
            await client.publish("PreToolUse", "Bash", {"command": "ls"})
    """
    client = HookBusClient(bus_address=bus_address, source=source)
    try:
        yield client
    finally:
        await client.close()


# Convenience function for simple use cases
async def publish(
    event_type: str,
    tool_name: str,
    tool_input: Optional[dict] = None,
    **kwargs
) -> dict:
    """
    Publish an event using a temporary client.
    
    This is a convenience function for simple one-off publishing.
    For high-frequency publishing, use HookBusClient directly.
    """
    async with HookBusClient(**kwargs) as client:
        return await client.publish(
            event_type=event_type,
            tool_name=tool_name,
            tool_input=tool_input
        )
