"""Opaque publisher fields survive the bus without policy-specific logic."""
import asyncio
import copy
import json

import aiohttp.web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from hookbus.bus import Bus, _auth_middleware
from hookbus.protocol import HookEvent


def envelope(**extensions):
    return dict(event_id='fixture-event', event_type='PreToolUse',
        timestamp='2026-09-05T06:00:00Z', source='fixture', session_id='fixture-session',
        tool_name='bash', tool_input={'command': 'echo fixture'}, **extensions)


@pytest.mark.parametrize('extensions', [
    {'action': 'write', 'resource_kind': 'file', 'resource': 'fixture.txt',
     'resource_scope': 'workspace', 'operation_risk': 'write'},
    {'resource': {'uri': 'fixture://object', 'nested': {'items': [1, None, False]}}},
    {'action': '', 'resource': None, 'operation_risk': False},
    {'vendor_extension': {'nested': ['opaque', {'future': True}]}},
    {'_extensions': {'event_id': 'opaque-not-authority'}},
])
def test_roundtrip_preserves_extension_values_and_wire_shape(extensions):
    raw = envelope(**extensions)
    original = copy.deepcopy(raw)
    event = HookEvent.from_dict(raw)
    output = json.loads(event.to_json())
    assert raw == original
    for key, value in raw.items():
        assert output[key] == value
    assert HookEvent.from_json(event.to_json()).to_dict() == output
    if '_extensions' not in raw:
        assert '_extensions' not in output


def test_legacy_create_does_not_add_extension_fields():
    event = HookEvent.create('PreToolUse', 'fixture', 'session', 'bash')
    assert set(event.to_dict()) == {'event_id', 'event_type', 'timestamp', 'source',
        'session_id', 'tool_name', 'tool_input', 'metadata', 'schema_version',
        'agent_id', 'correlation_id', 'annotations'}


def test_extensions_are_copied_and_cannot_shadow_transport_fields():
    raw = envelope(resource={'items': ['original']})
    event = HookEvent.from_dict(raw)
    raw['resource']['items'].append('source-change')
    assert event.to_dict()['resource']['items'] == ['original']
    output = event.to_dict()
    output['resource']['items'].append('output-change')
    assert event.to_dict()['resource']['items'] == ['original']
    event._extensions['event_id'] = 'forged'
    event._extensions['source'] = 'forged'
    assert event.to_dict()['event_id'] == 'fixture-event'
    assert event.to_dict()['source'] == 'fixture'


def test_authenticated_http_to_unix_subscriber_preserves_extensions(tmp_path, monkeypatch):
    monkeypatch.setenv('HOOKBUS_TOKEN', 'synthetic-extension-test')
    path = str(tmp_path / 'subscriber.sock')
    config = tmp_path / 'subscribers.yaml'
    config.write_text('subscribers:\n  - name: capture\n    type: sync\n'
        '    transport: unix_socket\n    address: ' + path + '\n'
        '    timeout: 2\n    retry_count: 1\n    events: [PreToolUse]\n')
    received = []

    async def run():
        async def subscriber(reader, writer):
            event = json.loads(await reader.readline())
            received.append(event)
            writer.write((json.dumps({'event_id': event['event_id'], 'subscriber': 'capture',
                'decision': 'deny', 'reason': 'Fixture decision'}) + '\n').encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(subscriber, path=path)
        bus = Bus(config_path=str(config), fail_open=False)
        app = aiohttp.web.Application(middlewares=[_auth_middleware])
        app.hookbus_token = 'synthetic-extension-test'
        app.hookbus_publisher_tokens = {}
        app.router.add_post('/event', bus.handle_http_request)
        raw = envelope(action='write', resource_kind='file', resource={'path': 'fixture.txt'},
            resource_scope='workspace', operation_risk='write', arbitrary={'future': [1, None]})
        try:
            async with TestClient(TestServer(app)) as client:
                response = await client.post('/event', json=raw,
                    headers={'Authorization': 'Bearer synthetic-extension-test'})
                assert response.status == 200
                decision = await response.json()
                assert decision['decision'] == 'deny'
                assert decision['event_id'] == raw['event_id']
            assert len(received) == 1
            for key, value in raw.items():
                assert received[0][key] == value
        finally:
            await bus.stop_server()
            server.close()
            await server.wait_closed()

    asyncio.run(run())
