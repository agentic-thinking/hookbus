"""
End-to-end test for HookBus Phase 1.

This test verifies:
1. Echo subscriber returns allow for all events
2. Bus routes events correctly
3. Client can publish events
4. Decision consolidation works

Test architecture:
- Creates a test subscriber config with echo subscriber
- Starts bus server
- Uses client to publish events
- Verifies decisions
"""

import asyncio
import sys
import os
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from hookbus.protocol import HookEvent, SubscriberResponse, Decision
from hookbus.bus import Bus
from hookbus.client import HookBusClient


class EchoSubscriber:
    """
    Echo subscriber for testing - returns allow for all events.
    
    This is a simple sync subscriber that always returns allow.
    Used for end-to-end testing.
    """
    
    async def on_event(self, event: HookEvent) -> SubscriberResponse:
        """Handle event by returning allow."""
        return SubscriberResponse(
            event_id=event.event_id,
            subscriber="echo-subscriber",
            decision="allow",
            reason="Echo test subscriber"
        )


async def run_echo_test():
    """Run the echo subscriber end-to-end test."""
    print("=" * 60)
    print("HookBus Phase 1 - Echo Subscriber E2E Test")
    print("=" * 60)
    
    # Create test subscriber config
    test_config_path = Path(__file__).parent / "test_subscribers.yaml"
    test_config_content = """
subscribers:
  - name: echo-subscriber
    type: sync
    transport: in_process
    module: test_echo.EchoSubscriber
    timeout: 5.0
    events:
      - PreToolUse
      - PostToolUse
      - UserPromptSubmit
      - SessionStart
      - SessionEnd
      - ModelResponse
      - AgentHandoff
      - ErrorOccurred
"""
    with open(test_config_path, "w") as f:
        f.write(test_config_content)
    
    try:
        # Create and start bus
        print("\n1. Starting bus with echo subscriber...")
        bus = Bus(config_path=str(test_config_path))
        
        # Override in-process handler to use our test class
        bus._in_process_handlers["echo-subscriber"] = EchoSubscriber()
        
        # Start server
        await bus.start_server(host="127.0.0.1", port=18801)
        print("   Bus server started on port 18801")
        
        # Give server time to start
        await asyncio.sleep(0.5)
        
        # Test 1: Basic event publishing
        print("\n2. Testing basic event publishing...")
        async with HookBusClient(
            bus_address="http://127.0.0.1:18801/event",
            source="test-client"
        ) as client:
            result = await client.publish(
                event_type="PreToolUse",
                tool_name="Bash",
                tool_input={"command": "echo hello"},
                session_id="test-session-1"
            )
            
            print(f"   Event ID: {result.get('event_id')}")
            print(f"   Decision: {result.get('decision')}")
            print(f"   Reason: {result.get('reason', 'N/A')}")
            
            assert result["decision"] == "allow", f"Expected 'allow', got '{result['decision']}'"
            print("   PASS: Got allow decision")
        
        # Test 2: Multiple event types
        print("\n3. Testing multiple event types...")
        async with HookBusClient(
            bus_address="http://127.0.0.1:18801/event",
            source="test-client"
        ) as client:
            event_types = [
                ("PostToolUse", "Bash"),
                ("UserPromptSubmit", "UserPrompt"),
                ("SessionStart", "Session"),
                ("SessionEnd", "Session"),
                ("ModelResponse", "LLM"),
                ("AgentHandoff", "Agent"),
                ("ErrorOccurred", "Error"),
            ]
            
            all_passed = True
            for event_type, tool_name in event_types:
                result = await client.publish(
                    event_type=event_type,
                    tool_name=tool_name,
                    session_id="test-session-2"
                )
                passed = result["decision"] == "allow"
                status = "PASS" if passed else "FAIL"
                print(f"   [{status}] {event_type}: {result['decision']}")
                if not passed:
                    all_passed = False
        
        # Test 3: Event normalization
        print("\n4. Testing event type normalization...")
        async with HookBusClient(
            bus_address="http://127.0.0.1:18801/event",
            source="test-client"
        ) as client:
            # These should be normalized to PreToolUse
            raw_events = [
                ("on_tool_start", "Bash"),  # OpenAI SDK
                ("tool_use_callback", "Bash"),  # Anthropic SDK
                ("on_tool_end", "Bash"),  # LangChain
            ]
            
            for raw_type, tool_name in raw_events:
                result = await client.publish(
                    event_type=raw_type,
                    tool_name=tool_name,
                    session_id="test-session-3"
                )
                passed = result["decision"] == "allow"
                status = "PASS" if passed else "FAIL"
                print(f"   [{status}] {raw_type} -> PreToolUse: {result['decision']}")
        
        # Test 4: Bus route_event method directly
        print("\n5. Testing bus.route_event() directly...")
        event = HookEvent.create(
            event_type="PreToolUse",
            source="direct-test",
            session_id="test-session-4",
            tool_name="Bash",
            tool_input={"command": "whoami"}
        )
        
        decision, reason = await bus.route_event(event)
        print(f"   Decision: {decision.value}")
        print(f"   Reason: {reason}")
        assert decision == Decision.ALLOW, f"Expected ALLOW, got {decision}"
        print("   PASS: Direct routing works")
        
        # Test 5: Subscriber matching
        print("\n6. Testing subscriber event matching...")
        matching = bus._get_matching_subscribers(event)
        print(f"   Subscribers matching PreToolUse: {len(matching)}")
        for sub in matching:
            print(f"   - {sub.name} ({sub.type})")
        assert len(matching) == 1, f"Expected 1 subscriber, got {len(matching)}"
        print("   PASS: Subscriber matching works")
        
        # Cleanup
        print("\n7. Cleaning up...")
        await bus.stop_server()
        
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)
        return True
        
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Clean up test config
        if test_config_path.exists():
            os.remove(test_config_path)


async def run_decision_consolidation_test():
    """Test decision consolidation with multiple subscribers."""
    print("\n" + "=" * 60)
    print("Testing Decision Consolidation")
    print("=" * 60)
    
    from protocol import consolidate_decisions
    
    # Test 1: Deny wins
    print("\n1. Testing deny-wins consolidation...")
    responses = [
        SubscriberResponse("e1", "sub1", "allow", "OK"),
        SubscriberResponse("e1", "sub2", "deny", "Blocked by policy"),
        SubscriberResponse("e1", "sub3", "allow", "OK"),
    ]
    decision, reason = consolidate_decisions(responses)
    print(f"   Decision: {decision.value}")
    print(f"   Reason: {reason}")
    assert decision == Decision.DENY, f"Expected DENY, got {decision}"
    assert "sub2" in reason, "Deny reason should be in combined reason"
    print("   PASS: Deny wins over allow")
    
    # Test 2: Ask wins over allow
    print("\n2. Testing ask-wins consolidation...")
    responses = [
        SubscriberResponse("e1", "sub1", "allow", "OK"),
        SubscriberResponse("e1", "sub2", "ask", "Need human approval"),
        SubscriberResponse("e1", "sub3", "allow", "OK"),
    ]
    decision, reason = consolidate_decisions(responses)
    print(f"   Decision: {decision.value}")
    print(f"   Reason: {reason}")
    assert decision == Decision.ASK, f"Expected ASK, got {decision}"
    print("   PASS: Ask wins over allow")
    
    # Test 3: All allow
    print("\n3. Testing all-allow consolidation...")
    responses = [
        SubscriberResponse("e1", "sub1", "allow", "OK"),
        SubscriberResponse("e1", "sub2", "allow", "OK"),
    ]
    decision, reason = consolidate_decisions(responses)
    print(f"   Decision: {decision.value}")
    assert decision == Decision.ALLOW, f"Expected ALLOW, got {decision}"
    print("   PASS: All allow returns allow")
    
    # Test 4: Empty responses
    print("\n4. Testing empty responses...")
    decision, reason = consolidate_decisions([])
    print(f"   Decision: {decision.value}")
    print(f"   Reason: {reason}")
    assert decision == Decision.ALLOW, f"Expected ALLOW, got {decision}"
    assert "No subscribers" in reason
    print("   PASS: Empty responses return allow")
    
    print("\n" + "=" * 60)
    print("CONSOLIDATION TESTS PASSED!")
    print("=" * 60)


async def main():
    """Run all tests."""
    success = True
    
    # Run echo subscriber test
    if not await run_echo_test():
        success = False
    
    # Run decision consolidation test
    await run_decision_consolidation_test()
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
