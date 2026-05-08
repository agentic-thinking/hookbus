import asyncio
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hookbus.bus import Bus
from hookbus.client import HookBusClient
from hookbus.protocol import HookEvent, SubscriberResponse


class ContextSubscriber:
    async def on_event(self, event: HookEvent) -> SubscriberResponse:
        return SubscriberResponse(
            event_id=event.event_id,
            subscriber="context-subscriber",
            decision="allow",
            reason="Context available",
            preprompt="Use SSH port 2204 with user admin for server 39.",
        )


def test_subscriber_preprompt_is_returned_as_first_class_context(tmp_path):
    async def run():
        os.environ["HOOKBUS_TOKEN"] = "test-preprompt-token"
        config_path = tmp_path / "subscribers.yaml"
        config_path.write_text(
            """
subscribers:
  - name: context-subscriber
    type: sync
    transport: in_process
    module: test_preprompt.ContextSubscriber
    timeout: 5.0
    events:
      - UserPromptSubmit
"""
        )

        bus = Bus(config_path=str(config_path))
        bus._in_process_handlers["context-subscriber"] = ContextSubscriber()
        await bus.start_server(host="127.0.0.1", port=18882)
        try:
            await asyncio.sleep(0.2)
            async with HookBusClient(
                bus_address="http://127.0.0.1:18882/event",
                source="test-client",
            ) as client:
                result = await client.publish(
                    event_type="UserPromptSubmit",
                    tool_name="UserPrompt",
                    tool_input={"prompt": "what is server 39"},
                    session_id="test-context-session",
                )
            assert result["decision"] == "allow"
            assert "Context available" in result["reason"]
            assert result["preprompt"] == "Use SSH port 2204 with user admin for server 39."
            assert result["additional_context"] == result["preprompt"]
        finally:
            await bus.stop_server()

    asyncio.run(run())
