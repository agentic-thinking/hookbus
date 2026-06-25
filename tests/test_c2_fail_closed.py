"""
Mythos C2 fail-closed smoke tests.

Verifies:
  AC-1: HOOKBUS_FAIL_OPEN env var (fail-closed by default)
  AC-2: Missing sync-subscriber response = deny (fail-closed) or allow (fail-open)
  AC-3: Hot reload swap-on-success (bad YAML preserves, good YAML swaps)
  AC-4: Missing gate responses cannot silently allow
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hookbus.bus import Bus
from hookbus.client import HookBusClient
from hookbus.protocol import HookEvent, SubscriberResponse


# ---------------------------------------------------------------------------
# In-process subscriber that always raises — simulates a downed gate
# ---------------------------------------------------------------------------
class FailingSubscriber:
    """Simulates a sync subscriber that crashes on every event."""

    async def on_event(self, event: HookEvent) -> SubscriberResponse:
        raise RuntimeError("simulated subscriber crash")


# ---------------------------------------------------------------------------
# In-process subscriber that always returns allow — used for hot-reload swap
# ---------------------------------------------------------------------------
class AllowSubscriber:
    """Simple allow-everything subscriber."""

    async def on_event(self, event: HookEvent) -> SubscriberResponse:
        return SubscriberResponse(
            event_id=event.event_id,
            subscriber="allow-gate",
            decision="allow",
            reason="always allow",
        )


# ---------------------------------------------------------------------------
# AC-2 / AC-4: fail-closed — missing sync subscriber returns DENY
# ---------------------------------------------------------------------------
def test_fail_closed_missing_subscriber_returns_deny(tmp_path):
    """When fail_open=False and a sync subscriber raises an exception,
    the bus must inject a synthetic DENY response and the consolidated
    decision must be DENY."""
    config_path = tmp_path / "subscribers.yaml"
    config_path.write_text("""
subscribers:
  - name: crash-gate
    type: sync
    transport: in_process
    module: test_c2_fail_closed.FailingSubscriber
    timeout: 0.5
    retry_count: 1
    retry_delay: 0.05
    events:
      - PreToolUse
""")

    async def run():
        os.environ["HOOKBUS_TOKEN"] = "test-fail-closed-token"
        bus = Bus(config_path=str(config_path), fail_open=False)
        bus._in_process_handlers["crash-gate"] = FailingSubscriber()
        await bus.start_server(host="127.0.0.1", port=18883)
        try:
            await asyncio.sleep(0.2)
            async with HookBusClient(
                bus_address="http://127.0.0.1:18883/event",
                source="test-client",
            ) as client:
                result = await client.publish(
                    event_type="PreToolUse",
                    tool_name="Bash",
                    tool_input={"command": "rm -rf /"},
                    session_id="test-fail-closed",
                )
            assert result["decision"] == "deny", (
                f"Expected DENY when sync subscriber crashes, got {result['decision']}"
            )
            assert "failed to respond" in result.get("reason", ""), (
                f"Reason should mention subscriber failure, got {result.get('reason', '')}"
            )
        finally:
            await bus.stop_server()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# AC-1 / AC-4: fail-open — opt-in escape hatch preserves old behaviour
# ---------------------------------------------------------------------------
def test_fail_open_missing_subscriber_returns_allow(tmp_path):
    """When fail_open=True and a sync subscriber raises an exception,
    the bus must inject a synthetic ALLOW response and the consolidated
    decision must be ALLOW."""
    config_path = tmp_path / "subscribers.yaml"
    config_path.write_text("""
subscribers:
  - name: crash-gate
    type: sync
    transport: in_process
    module: test_c2_fail_closed.FailingSubscriber
    timeout: 0.5
    retry_count: 1
    retry_delay: 0.05
    events:
      - PreToolUse
""")

    async def run():
        os.environ["HOOKBUS_TOKEN"] = "test-fail-open-token"
        bus = Bus(config_path=str(config_path), fail_open=True)
        bus._in_process_handlers["crash-gate"] = FailingSubscriber()
        await bus.start_server(host="127.0.0.1", port=18884)
        try:
            await asyncio.sleep(0.2)
            async with HookBusClient(
                bus_address="http://127.0.0.1:18884/event",
                source="test-client",
            ) as client:
                result = await client.publish(
                    event_type="PreToolUse",
                    tool_name="Bash",
                    tool_input={"command": "rm -rf /"},
                    session_id="test-fail-open",
                )
            assert result["decision"] == "allow", (
                f"Expected ALLOW when fail_open=True, got {result['decision']}"
            )
            assert "failed to respond" in result.get("reason", ""), (
                f"Reason should mention subscriber failure, got {result.get('reason', '')}"
            )
        finally:
            await bus.stop_server()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# AC-3 / AC-4: hot reload — bad YAML preserves current subscribers
# ---------------------------------------------------------------------------
def test_hot_reload_bad_yaml_preserves_subscribers(tmp_path):
    """Overwriting subscribers.yaml with invalid YAML and calling
    reload_config must preserve the currently-loaded subscribers."""
    config_path = tmp_path / "subscribers.yaml"
    config_path.write_text("""
subscribers:
  - name: gate-alpha
    type: sync
    transport: in_process
    module: test_c2_fail_closed.AllowSubscriber
    timeout: 5.0
    events:
      - PreToolUse
""")

    # Enterprise licence so hot_reload is enabled
    os.environ["HOOKBUS_LICENSE"] = "ent-test-2099-12-31"
    try:
        bus = Bus(config_path=str(config_path))
        assert len(bus.subscribers) == 1
        assert bus.subscribers[0].name == "gate-alpha"

        # Overwrite with truly invalid YAML (causes ParserError)
        config_path.write_text("][\n")

        # Call reload_config; we must run it inside an event loop
        async def _reload():
            await bus.reload_config()

        asyncio.run(_reload())

        # Subscribers must be preserved — no swap happened
        assert len(bus.subscribers) == 1, (
            f"Bad YAML reload must preserve subscribers, got {len(bus.subscribers)}"
        )
        assert bus.subscribers[0].name == "gate-alpha", (
            f"Bad YAML reload must preserve subscriber name, got {bus.subscribers[0].name}"
        )
    finally:
        os.environ.pop("HOOKBUS_LICENSE", None)


# ---------------------------------------------------------------------------
# AC-3 / AC-4: hot reload — good YAML swaps subscribers
# ---------------------------------------------------------------------------
def test_hot_reload_good_yaml_swaps_subscribers(tmp_path):
    """Overwriting subscribers.yaml with valid YAML (new subscriber list)
    and calling reload_config must swap to the new subscribers."""
    config_path = tmp_path / "subscribers.yaml"
    config_path.write_text("""
subscribers:
  - name: gate-alpha
    type: sync
    transport: in_process
    module: test_c2_fail_closed.AllowSubscriber
    timeout: 5.0
    events:
      - PreToolUse
""")

    os.environ["HOOKBUS_LICENSE"] = "ent-test-2099-12-31"
    try:
        bus = Bus(config_path=str(config_path))
        assert len(bus.subscribers) == 1
        assert bus.subscribers[0].name == "gate-alpha"

        # Overwrite with new valid subscriber list
        config_path.write_text("""
subscribers:
  - name: gate-beta
    type: sync
    transport: in_process
    module: test_c2_fail_closed.AllowSubscriber
    timeout: 5.0
    events:
      - PreToolUse
  - name: gate-gamma
    type: async
    transport: in_process
    module: test_c2_fail_closed.AllowSubscriber
    timeout: 3.0
    events:
      - PostToolUse
""")

        async def _reload():
            await bus.reload_config()

        asyncio.run(_reload())

        # Subscribers must have swapped
        names = [s.name for s in bus.subscribers]
        assert len(bus.subscribers) == 2, (
            f"Good YAML reload must swap in 2 subscribers, got {len(bus.subscribers)}"
        )
        assert "gate-beta" in names, f"Expected gate-beta in reloaded subscribers, got {names}"
        assert "gate-gamma" in names, f"Expected gate-gamma in reloaded subscribers, got {names}"
        assert "gate-alpha" not in names, (
            f"gate-alpha should not survive good-YAML reload, got {names}"
        )
    finally:
        os.environ.pop("HOOKBUS_LICENSE", None)
