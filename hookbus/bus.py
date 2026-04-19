"""
HookBus - Universal async event bus for AI agent lifecycle enforcement.

The bus receives events from publishers and routes them to registered subscribers.
It handles fan-out, transport abstraction, and decision consolidation.

Bus is stateless - it routes events but stores nothing.

Version: 1.0
"""

import asyncio
import signal
import json
import logging
import os
import socket
import importlib
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import yaml
import aiohttp
import aiohttp.web

from .licence import load_licence, banner
from .dashboard import DashboardState, register_dashboard_routes
import secrets

# HookBus authentication token.
# Priority: HOOKBUS_TOKEN env var > /root/.hookbus/.token file > generated (first run)
_TOKEN_PATH = Path(os.environ.get('HOOKBUS_TOKEN_PATH', '/root/.hookbus/.token'))


def _load_or_generate_token() -> str:
    env_tok = os.environ.get('HOOKBUS_TOKEN', '').strip()
    if env_tok:
        # Subscribers on the shared volume read /root/.hookbus/.token to pick up
        # the bearer token. If we only hold it in-process, they hang waiting for
        # the file. Persist env-sourced tokens to disk so the whole stack agrees.
        try:
            _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            _TOKEN_PATH.write_text(env_tok)
            _TOKEN_PATH.chmod(0o600)
        except Exception as exc:
            logger.warning('could not persist env token to %s: %s', _TOKEN_PATH, exc)
        return env_tok
    try:
        if _TOKEN_PATH.exists():
            return _TOKEN_PATH.read_text().strip()
    except Exception:
        pass
    tok = secrets.token_urlsafe(32)
    try:
        _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_PATH.write_text(tok)
        _TOKEN_PATH.chmod(0o600)
    except Exception as exc:
        logger.warning('could not persist token to %s: %s', _TOKEN_PATH, exc)
    if not tok:
        raise SystemExit(
            'FATAL: HookBus could not obtain or generate an auth token. '
            'Refusing to start without authentication.'
        )
    return tok



# --- Subscriber URL validation (SSRF guard) -------------------------------
# Block requests to cloud metadata endpoints and local-only ranges.
# Operators can relax this per-host via HOOKBUS_SUBSCRIBER_ALLOW_PRIVATE=1
# when running on a trusted cluster network where subscribers live on the
# same VPC/overlay and private addresses are legitimate.
import ipaddress as _ipaddress
from urllib.parse import urlparse as _urlparse

_SSRF_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Cloud metadata endpoints (AWS/GCP/Azure/Alibaba/Oracle)
_SSRF_BLOCKED_HOSTS = frozenset({
    "169.254.169.254",            # AWS, DigitalOcean, GCP alt, Azure IMDS
    "metadata.google.internal",   # GCP canonical
    "100.100.100.200",            # Alibaba Cloud
    "fd00:ec2::254",              # AWS IMDSv2 IPv6
})

# Private-range blocks. Turned OFF when HOOKBUS_SUBSCRIBER_ALLOW_PRIVATE=1
# because most Compose/K8s/VPC deployments legitimately use these.
_SSRF_BLOCK_PRIVATE = os.environ.get(
    "HOOKBUS_SUBSCRIBER_ALLOW_PRIVATE", ""
).strip().lower() not in {"1", "true", "yes"}


def _validate_subscriber_address(address: str) -> None:
    """Raise ValueError if address is unsafe to call from the bus.
    Always blocks cloud-metadata endpoints. Blocks private-IP ranges unless
    HOOKBUS_SUBSCRIBER_ALLOW_PRIVATE=1 is set (the common case for Compose
    and K8s where subscribers live on a private overlay)."""
    if not isinstance(address, str) or not address:
        raise ValueError("subscriber address must be a non-empty string")
    parsed = _urlparse(address)
    if parsed.scheme not in _SSRF_ALLOWED_SCHEMES:
        raise ValueError(
            f"subscriber scheme '{parsed.scheme}' not permitted "
            f"(allowed: {sorted(_SSRF_ALLOWED_SCHEMES)})"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("subscriber URL missing hostname")
    if host in _SSRF_BLOCKED_HOSTS:
        raise ValueError(f"subscriber hostname '{host}' is a cloud metadata endpoint, blocked")
    # IP-literal checks: block link-local and metadata-alias ranges unconditionally
    try:
        ip = _ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_link_local:
            raise ValueError(f"subscriber IP {ip} is link-local, blocked (SSRF)")
        if ip.is_loopback and _SSRF_BLOCK_PRIVATE:
            raise ValueError(f"subscriber IP {ip} is loopback, blocked (set HOOKBUS_SUBSCRIBER_ALLOW_PRIVATE=1 to permit)")
        if ip.is_private and _SSRF_BLOCK_PRIVATE:
            raise ValueError(f"subscriber IP {ip} is private, blocked (set HOOKBUS_SUBSCRIBER_ALLOW_PRIVATE=1 to permit)")
    # -------------------------------------------------------------------

@aiohttp.web.middleware
async def _auth_middleware(request, handler):
    # Exempt: OPTIONS for CORS preflight (if any)
    if request.method == 'OPTIONS':
        return await handler(request)
    token = getattr(request.app, 'hookbus_token', '')
    if not token:
        # Auth must be configured. Refuse to serve if startup did not set a token.
        return aiohttp.web.json_response(
            {'error': 'server misconfigured: auth token unavailable'},
            status=503,
        )
    # Accept Authorization: Bearer <token>
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer ') and secrets.compare_digest(auth[7:], token):
        return await handler(request)
    # Accept ?token=... for dashboard bookmark UX, then set a cookie
    q_tok = request.query.get('token', '')
    if q_tok and secrets.compare_digest(q_tok, token):
        resp = await handler(request)
        resp.set_cookie('hookbus_token', token, httponly=True, samesite='Lax', path='/')
        return resp
    # Accept session cookie (set after query-param entry)
    c_tok = request.cookies.get('hookbus_token', '')
    if c_tok and secrets.compare_digest(c_tok, token):
        return await handler(request)
    return aiohttp.web.json_response({'error': 'unauthorised', 'hint': 'supply Authorization: Bearer <token> header or ?token= query'}, status=401)

from .protocol import (
    HookEvent,
    SubscriberResponse,
    Decision,
    DateTimeEncoder,
    consolidate_decisions
)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class SubscriberConfig:
    """Configuration for a single subscriber."""
    name: str
    type: str
    transport: str
    address: str = ""
    module: str = ""
    timeout: float = 5.0
    retry_count: int = 1
    retry_delay: float = 0.1
    events: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        """Validate subscriber configuration."""
        # Subscriber type must be "sync" or "async"
        if self.type not in ("sync", "async"):
            raise ValueError(
                f"Invalid subscriber type '{self.type}' for '{self.name}'. "
                f"Must be 'sync' or 'async'."
            )
        
        # Transport validation
        valid_transports = ("unix_socket", "http", "in_process")
        if self.transport not in valid_transports:
            raise ValueError(
                f"Invalid transport '{self.transport}' for '{self.name}'. "
                f"Must be one of: {valid_transports}"
            )
        
        # Address required for socket/http transports
        if self.transport in ("unix_socket", "http") and not self.address:
            raise ValueError(
                f"Subscriber '{self.name}' requires an address for "
                f"{self.transport} transport."
            )
        
        # Module required for in_process transport
        if self.transport == "in_process" and not self.module:
            raise ValueError(
                f"Subscriber '{self.name}' requires a module path for "
                f"in_process transport."
            )


class Bus:
    """
    Universal async event bus for AI agent lifecycle enforcement.
    
    The bus routes events to registered subscribers and consolidates
    decisions from sync subscribers using deny-wins logic.
    
    Attributes:
        config_path: Path to subscriber configuration file
        bus_address: HTTP address for receiving events
        fail_open: If True, timeout returns allow; if False, returns deny
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        bus_address: str = "http://localhost:18800/event",
        fail_open: bool = True
    ):
        """
        Initialize the bus.
        
        Args:
            config_path: Path to subscribers.yaml. Defaults to ~/.hookbus/subscribers.yaml
            bus_address: HTTP address for the bus endpoint
            fail_open: If True, timeout returns allow; if False, returns deny
        """
        if config_path is None:
            home = Path.home()
            config_path = str(home / ".hookbus" / "subscribers.yaml")
        
        self.config_path = config_path
        self.bus_address = bus_address
        self.fail_open = fail_open
        # Dashboard state, populated in route_event after each decision
        self.dashboard = DashboardState()
        # Auth token for forwarding events to subscribers (shared token model)
        self._bus_token = _load_or_generate_token()
        # HookBus tier + licence (Light vs Enterprise)
        self.licence = load_licence()
        if not self.licence.is_enterprise():
            logger.info("hookbus: Light tier - some features disabled (hot_reload, advanced_consolidation, failover_groups, etc.). Upgrade: agenticthinking.uk")
        
        self._subscribers: list[SubscriberConfig] = []
        self._in_process_handlers: dict[str, object] = {}
        self._running = False
        self._server: Optional[aiohttp.web.Application] = None
        
        self._load_config()

    def _forward_headers(self) -> dict:
        """Return Authorization header for bus→subscriber forwards."""
        h = {}
        if self._bus_token:
            h['Authorization'] = f'Bearer {self._bus_token}'
        return h

    def _load_config(self) -> None:
        """Load subscriber configuration from YAML file."""
        config_file = Path(self.config_path)
        
        if not config_file.exists():
            # Try relative path for testing
            config_file = Path(__file__).parent / "subscribers.yaml"
        
        if not config_file.exists():
            logger.warning(f"Config file not found: {self.config_path}")
            return
        
        with open(config_file, "r") as f:
            config = yaml.safe_load(f)
        
        subscribers = config.get("subscribers", [])
        for sub_config in subscribers:
            try:
                subscriber = SubscriberConfig(**sub_config)
                self._subscribers.append(subscriber)
                logger.info(f"Loaded subscriber: {subscriber.name} ({subscriber.type})")
                
                # Pre-load in-process handlers
                if subscriber.transport == "in_process":
                    self._load_in_process_handler(subscriber)
                    
            except ValueError as e:
                logger.error(f"Invalid subscriber config: {e}")

    def _load_in_process_handler(self, subscriber: SubscriberConfig) -> None:
        """Load an in-process subscriber module."""
        try:
            module_path, class_name = subscriber.module.rsplit(".", 1)
            module = importlib.import_module(module_path)
            handler_class = getattr(module, class_name)
            self._in_process_handlers[subscriber.name] = handler_class()
            logger.info(f"Loaded in-process handler: {subscriber.name}")
        except Exception as e:
            logger.error(f"Failed to load in-process handler {subscriber.name}: {e}")

    def _get_matching_subscribers(self, event: HookEvent) -> list[SubscriberConfig]:
        """Get all subscribers that handle this event type."""
        matching = []
        for subscriber in self._subscribers:
            if event.event_type in subscriber.events:
                matching.append(subscriber)
        return matching

    async def _send_to_sync_subscriber(
        self,
        subscriber: SubscriberConfig,
        event: HookEvent
    ) -> Optional[SubscriberResponse]:
        """
        Send event to a sync subscriber and wait for response.
        
        Args:
            subscriber: The subscriber configuration
            event: The event to send
            
        Returns:
            SubscriberResponse or None on timeout/error
        """
        for attempt in range(subscriber.retry_count):
            try:
                if subscriber.transport == "unix_socket":
                    return await self._send_unix_socket(subscriber, event)
                elif subscriber.transport == "http":
                    return await self._send_http(subscriber, event)
                elif subscriber.transport == "in_process":
                    return await self._send_in_process(subscriber, event)
            except Exception as e:
                logger.warning(
                    f"Attempt {attempt + 1}/{subscriber.retry_count} failed "
                    f"for {subscriber.name}: {e}"
                )
                if attempt < subscriber.retry_count - 1:
                    await asyncio.sleep(subscriber.retry_delay)
        
        logger.error(f"All attempts failed for subscriber {subscriber.name}")
        return None

    async def _send_to_async_subscriber(
        self,
        subscriber: SubscriberConfig,
        event: HookEvent
    ) -> None:
        """
        Send event to an async subscriber without waiting.
        
        Args:
            subscriber: The subscriber configuration
            event: The event to send
        """
        try:
            if subscriber.transport == "unix_socket":
                await self._send_unix_socket(subscriber, event)
            elif subscriber.transport == "http":
                await self._send_http(subscriber, event)
            elif subscriber.transport == "in_process":
                await self._send_in_process(subscriber, event)
        except Exception as e:
            logger.error(f"Async subscriber {subscriber.name} failed: {e}")

    async def _send_unix_socket(
        self,
        subscriber: SubscriberConfig,
        event: HookEvent
    ) -> Optional[SubscriberResponse]:
        """Send event via Unix domain socket."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(subscriber.address),
            timeout=subscriber.timeout
        )
        
        try:
            # Send event as JSON
            event_json = event.to_json() + "\n"
            writer.write(event_json.encode())
            await writer.drain()
            
            # Read response
            response_line = await asyncio.wait_for(
                reader.readline(),
                timeout=subscriber.timeout
            )
            
            if response_line:
                response = SubscriberResponse.from_json(response_line.decode().strip())
                logger.debug(f"Got response from {subscriber.name}: {response.decision}")
                return response
            return None
        finally:
            writer.close()
            await writer.wait_closed()

    async def _send_http(
        self,
        subscriber: SubscriberConfig,
        event: HookEvent
    ) -> Optional[SubscriberResponse]:
        """Send event via HTTP."""
        _validate_subscriber_address(subscriber.address)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                subscriber.address,
                json=event.to_dict(),
                headers=self._forward_headers(),
                timeout=aiohttp.ClientTimeout(total=subscriber.timeout)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return SubscriberResponse.from_dict(data)
                elif response.status == 204:
                    return None  # Async subscriber
                else:
                    raise Exception(f"HTTP error: {response.status}")

    async def _send_in_process(
        self,
        subscriber: SubscriberConfig,
        event: HookEvent
    ) -> Optional[SubscriberResponse]:
        """Send event to in-process subscriber."""
        handler = self._in_process_handlers.get(subscriber.name)
        if handler is None:
            raise Exception(f"In-process handler not found: {subscriber.name}")
        
        # Call the handler
        result = await handler.on_event(event)
        
        if result is not None:
            # Convert to response format
            if isinstance(result, SubscriberResponse):
                return result
            elif isinstance(result, dict):
                return SubscriberResponse.from_dict(result)
            elif isinstance(result, str):
                # Simple string decision
                return SubscriberResponse(
                    event_id=event.event_id,
                    subscriber=subscriber.name,
                    decision=result
                )
        
        return None  # Async subscriber returned None

    async def route_event(self, event: HookEvent) -> tuple[Decision, str]:
        """
        Route an event to all matching subscribers and consolidate decisions.
        
        This is the main entry point for event processing.
        
        Args:
            event: The event to route
            
        Returns:
            Tuple of (consolidated decision, combined reason)
        """
        import time
        _t0 = time.time()
        matching = self._get_matching_subscribers(event)
        if not matching:
            logger.debug(f"No subscribers for event type: {event.event_type}")
            self.dashboard.record_event(event, Decision.ALLOW, "No subscribers matched", responses=[], latency_ms=(time.time()-_t0)*1000.0)
            return Decision.ALLOW, "No subscribers matched"
        
        logger.info(
            f"Routing event {event.event_id} ({event.event_type}) "
            f"to {len(matching)} subscribers"
        )
        
        sync_subscribers = [s for s in matching if s.type == "sync"]
        async_subscribers = [s for s in matching if s.type == "async"]
        
        responses: list[SubscriberResponse] = []
        
        # Fan out to sync subscribers in parallel
        if sync_subscribers:
            sync_tasks = [
                self._send_to_sync_subscriber(subscriber, event)
                for subscriber in sync_subscribers
            ]
            
            try:
                sync_responses = await asyncio.wait_for(
                    asyncio.gather(*sync_tasks, return_exceptions=True),
                    timeout=max(s.timeout for s in sync_subscribers) + 1
                )
                
                for response in sync_responses:
                    if isinstance(response, SubscriberResponse):
                        responses.append(response)
                    elif isinstance(response, Exception):
                        logger.error(f"Sync subscriber failed: {response}")
                        
            except asyncio.TimeoutError:
                logger.warning("Sync subscriber timeout")
                if not self.fail_open:
                    self.dashboard.record_event(event, Decision.DENY, "Timeout exceeded", responses=responses, latency_ms=(time.time()-_t0)*1000.0)
                    return Decision.DENY, "Timeout exceeded"
        
        # Fan out to async subscribers without waiting
        for subscriber in async_subscribers:
            asyncio.create_task(
                self._send_to_async_subscriber(subscriber, event)
            )
        
        # Consolidate decisions
        decision, reason = consolidate_decisions(responses)
        self.dashboard.record_event(event, decision, reason, responses=responses, latency_ms=(time.time()-_t0)*1000.0)
        return decision, reason

    async def handle_http_request(
        self,
        request: aiohttp.web.Request
    ) -> aiohttp.web.Response:
        """Handle incoming HTTP request."""
        try:
            data = await request.json()
            event = HookEvent.from_dict(data)
            
            decision, reason = await self.route_event(event)
            
            return aiohttp.web.json_response({
                "event_id": event.event_id,
                "decision": decision.value,
                "reason": reason
            })
            
        except Exception as e:
            logger.exception("Request handling error")
            return aiohttp.web.json_response(
                {"error": "internal server error"},
                status=500
            )

    async def start_server(self, host: str = "0.0.0.0", port: int = 18800) -> None:
        """Start the HookBus HTTP server, print the boot banner, bind the /event endpoint and dashboard routes, and install SIGHUP hot-reload."""
        # HookBus startup banner, tier + patent + licence
        print(banner(self.licence, "1.0.0"), flush=True)
        """Start the HTTP server for receiving events."""
        self._running = True
        # Load or generate auth token before building app
        token = _load_or_generate_token()
        # Advertise where to read the token. Never log the token itself ,
        # `docker logs hookbus` is readable by anyone with container/host
        # access, so printing it there re-creates an auth bypass.
        logger.info('=' * 60)
        logger.info('  HookBus authentication required on every request.')
        logger.info('  Read the token:  docker exec hookbus cat %s', _TOKEN_PATH)
        logger.info('  Then open:       http://%s:%s/?token=<paste>', host if host != '0.0.0.0' else 'localhost', port)
        logger.info('  Publishers use:  Authorization: Bearer <token>')
        logger.info('  Pin in production: set HOOKBUS_TOKEN env before first boot.')
        logger.info('=' * 60)
        app = aiohttp.web.Application(middlewares=[_auth_middleware])
        app.hookbus_token = token
        app.router.add_post("/event", self.handle_http_request)
        # Register HookBus Light dashboard routes (GET /, /api/stats, /api/events,
        # /api/subscribers, /api/publishers) on the same aiohttp app.
        register_dashboard_routes(app, self)
        
        runner = aiohttp.web.AppRunner(app)
        await runner.setup()
        
        site = aiohttp.web.TCPSite(runner, host, port)
        await site.start()
        
        self._server = app
        
        logger.info(f"Bus server started on {host}:{port}")
        logger.info(f"Endpoint: http://{host}:{port}/event")

        # Hot reload on SIGHUP
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(
            signal.SIGHUP,
            lambda: asyncio.ensure_future(self.reload_config())
        )
        logger.info("Hot reload: send SIGHUP to reload subscribers.yaml")

    async def stop_server(self) -> None:
        """Stop the HTTP server."""
        self._running = False
        logger.info("Bus server stopped")

    async def reload_config(self) -> None:
        """Reload the subscribers.yaml configuration at runtime (hot reload, paid tier only)."""
        if not self.licence.has("hot_reload"):
            logger.warning(
                "hookbus: hot_reload disabled in Light tier. Restart container to update config. Upgrade: agenticthinking.uk"
            )
            return
        """Reload subscriber configuration (hot reload)."""
        logger.info("Reloading subscriber configuration...")
        self._subscribers.clear()
        self._in_process_handlers.clear()
        self._load_config()

    @property
    def subscribers(self) -> list[SubscriberConfig]:
        """Get list of configured subscribers."""
        return self._subscribers.copy()

    @property
    def is_running(self) -> bool:
        """Check if the bus server is running."""
        return self._running


def _run_provisioner() -> None:
    """Detect installed agents and provision their HookBus publishers.

    Failures never stop the bus from starting; they are logged and the
    bus runs without that publisher. Fail-open on provisioning so a
    broken bundle does not take the bus itself down."""
    from pathlib import Path
    from .publishers.registry import REGISTRY, detect_agents
    from .publishers.provisioner import provision_agent, OptOut
    from .publishers.state import StateLog

    state_dir = Path.home() / ".hookbus"
    optout = OptOut(state_dir / "opt-out.json")
    state = StateLog(state_dir / "state" / "provisioned.json")

    for det in detect_agents(REGISTRY):
        if optout.is_opted_out(det.agent):
            logger.info(f"Provisioner: {det.agent} opted-out, skipping")
            continue
        bundle_dir = Path(__file__).resolve().parent / "publishers" / "bundles" / det.agent
        if not bundle_dir.exists():
            logger.warning(f"Provisioner: no bundle for {det.agent}")
            continue
        install_parent = Path.home() / Path(det.relative_config_path).parent
        try:
            provision_agent(
                agent=det.agent,
                bundle_dir=bundle_dir,
                install_dest_dir=install_parent,
                state=state,
                bundle_version="0.2.0",
            )
            logger.info(f"Provisioner: {det.agent} installed -> {install_parent}")
        except Exception as e:
            logger.error(f"Provisioner: {det.agent} failed: {e}")


def _run_server() -> None:
    """Boot the HTTP bus and block forever."""
    import os
    port = int(os.environ.get("HOOKBUS_PORT", "18800"))
    bus = Bus()

    async def _boot():
        await bus.start_server(host="0.0.0.0", port=port)
        while True:
            await asyncio.sleep(3600)

    try:
        asyncio.run(_boot())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


def main(argv=None) -> None:
    """Entrypoint for the `hookbus` CLI, auto-provisions publishers (unless `--no-provision`) and runs the bus server."""
    import argparse
    parser = argparse.ArgumentParser(prog="hookbus")
    parser.add_argument("--no-provision", action="store_true",
                        help="Skip auto-install of publishers into detected agents")
    args = parser.parse_args(argv)
    if not args.no_provision:
        _run_provisioner()
    _run_server()
