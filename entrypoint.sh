#!/bin/bash
set -e

CONFIG="${HOOKBUS_CONFIG:-/root/.hookbus/subscribers.yaml}"
PORT="${HOOKBUS_PORT:-18800}"

# Copy default config if none exists
if [ ! -f "$CONFIG" ]; then
    cp /opt/hookbus/hookbus.yaml "$CONFIG" 2>/dev/null || true
    echo "Default subscribers.yaml copied to $CONFIG"
fi

echo "HookBus starting on :$PORT"
echo "Config: $CONFIG"

exec python3 -c "
import asyncio, sys, os, signal
sys.path.insert(0, '/opt/hookbus')
os.environ['PYTHONPATH'] = '/opt/hookbus'

import importlib.util
spec = importlib.util.spec_from_file_location('protocol', '/opt/hookbus/hookbus/protocol.py')
protocol = importlib.util.module_from_spec(spec)
sys.modules['protocol'] = protocol
spec.loader.exec_module(protocol)

from hookbus.bus import Bus

async def main():
    bus = Bus(config_path='$CONFIG', bus_address='0.0.0.0:$PORT')
    await bus.start_server(host='0.0.0.0', port=$PORT)
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await bus.stop_server()

asyncio.run(main())
"
