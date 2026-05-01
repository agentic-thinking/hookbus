from __future__ import annotations

import collections
import re
import time
from typing import Any, Dict, Optional

import aiohttp.web

__all__ = ["BusState", "register_api_routes"]

_RING_SIZE = 500

# Regex to pull subscriber names from reason strings. Reason strings
# carry a "[subscriber-name] ..." prefix emitted by each subscriber.
_REASON_NAME = re.compile(r"\[([a-z0-9][a-z0-9_\-]{0,63})\]")


def _empty_counts() -> Dict[str, int]:
    return {"total": 0, "allow": 0, "deny": 0, "ask": 0}


class BusState:
    """In-memory event + stats state for the API."""

    def __init__(self) -> None:
        self._events: "collections.deque[dict]" = collections.deque(maxlen=_RING_SIZE)
        self._next_id = 1
        self._stats = _empty_counts()
        self._subscriber_health: Dict[str, Dict[str, Any]] = {}
        self._subscriber_counts: Dict[str, Dict[str, int]] = {}
        self._subscriber_latency: Dict[str, "collections.deque[float]"] = {}
        self._publishers: Dict[str, float] = {}
        self._start_time = time.time()
        self._minute_window: "collections.deque[float]" = collections.deque(maxlen=2000)

    def _bump_subscriber(self, name: str, decision_str: str, latency_ms: float) -> None:
        counts = self._subscriber_counts.setdefault(name, _empty_counts())
        counts["total"] += 1
        if decision_str in counts:
            counts[decision_str] += 1
        buf = self._subscriber_latency.setdefault(name, collections.deque(maxlen=200))
        if latency_ms:
            buf.append(float(latency_ms))

    def record_event(
        self,
        event,
        decision,
        reason: str,
        responses: Optional[list] = None,
        latency_ms: float = 0.0,
    ) -> None:
        """Record a bus decision for the live API, update rolling counters, per-subscriber stats, and the recent-events ring buffer."""
        ts = time.time()
        self._minute_window.append(ts)
        decision_str = getattr(decision, "value", str(decision)).lower()
        self._stats["total"] += 1
        if decision_str in self._stats:
            self._stats[decision_str] += 1
        source = getattr(event, "source", "") or ""
        if source:
            self._publishers[source] = ts
        # Per-subscriber stats: prefer structured responses, fall back to reason-string parse
        credited = False
        if responses:
            for r in responses:
                name = getattr(r, "subscriber", None) or getattr(r, "name", None)
                if not name:
                    continue
                d = getattr(r, "decision", None)
                d_str = getattr(d, "value", str(d)).lower() if d is not None else decision_str
                r_latency = getattr(r, "latency_ms", None) or latency_ms
                self._bump_subscriber(name, d_str, r_latency)
                self._subscriber_health[name] = {
                    "last_response_ms": float(r_latency) if r_latency else None,
                    "last_seen": ts,
                    "last_decision": d_str,
                }
                credited = True
        if not credited and reason:
            for name in _REASON_NAME.findall(reason):
                self._bump_subscriber(name, decision_str, latency_ms)
                self._subscriber_health.setdefault(name, {})
                self._subscriber_health[name].update({
                    "last_response_ms": float(latency_ms) if latency_ms else None,
                    "last_seen": ts,
                    "last_decision": decision_str,
                })
        _meta = getattr(event, "metadata", {}) or {}
        _reasoning = _meta.get("reasoning_content") if isinstance(_meta, dict) else None
        _reasoning_chars = _meta.get("reasoning_chars", len(_reasoning or "")) if isinstance(_meta, dict) else 0
        response_details = []
        if responses:
            for r in responses:
                d = getattr(r, "decision", None)
                response_details.append({
                    "subscriber": getattr(r, "subscriber", None) or getattr(r, "name", "") or "",
                    "decision": getattr(d, "value", str(d)).lower() if d is not None else "",
                    "reason": getattr(r, "reason", "") or "",
                    "metadata": getattr(r, "metadata", {}) or {},
                    "latency_ms": getattr(r, "latency_ms", None),
                })
        self._events.appendleft({
            "id": self._next_id,
            "ts": ts,
            "event_id": getattr(event, "event_id", "") or "",
            "session_id": getattr(event, "session_id", "") or "",
            "correlation_id": getattr(event, "correlation_id", "") or "",
            "source": source,
            "tool_name": getattr(event, "tool_name", "") or "",
            "tool_input": getattr(event, "tool_input", {}) or {},
            "event_type": getattr(event, "event_type", "") or "",
            "decision": decision_str,
            "reason": (reason or "")[:240],
            "latency_ms": round(latency_ms, 1),
            "metadata": _meta if isinstance(_meta, dict) else {},
            "subscriber_responses": response_details,
            "reasoning_chars": int(_reasoning_chars or 0),
            "has_reasoning": bool(_reasoning),
        })
        self._next_id += 1

    def stats(self) -> dict:
        """Return a JSON-serialisable snapshot of bus stats (decision counters, events-per-minute, uptime) for the HTTP API."""
        cutoff = time.time() - 60
        epm = sum(1 for t in self._minute_window if t >= cutoff)
        return {
            **self._stats,
            "events_per_min": epm,
            "uptime_s": int(time.time() - self._start_time),
        }

    def events(self, since: int = 0) -> list:
        """Return recent events from the ring buffer with id greater than `since`, for dashboard polling."""
        return [e for e in self._events if e["id"] > since]

    def subscriber_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """Combined health + counts + avg latency for each subscriber the bus has seen."""
        out: Dict[str, Dict[str, Any]] = {}
        names = set(self._subscriber_counts) | set(self._subscriber_health)
        for name in names:
            lat_buf = self._subscriber_latency.get(name)
            avg_lat = round(sum(lat_buf) / len(lat_buf), 1) if lat_buf else None
            out[name] = {
                **self._subscriber_health.get(name, {}),
                "counts": self._subscriber_counts.get(name, _empty_counts()),
                "avg_latency_ms": avg_lat,
            }
        return out

    def publishers(self) -> Dict[str, float]:
        return dict(self._publishers)


def register_api_routes(app: aiohttp.web.Application, bus) -> None:
    """Attach JSON API routes to an existing aiohttp app."""

    state = bus.state

    async def api_stats(_request):
        return aiohttp.web.json_response(state.stats())

    async def api_events(request):
        try:
            since = int(request.query.get("since", "0"))
        except ValueError:
            since = 0
        return aiohttp.web.json_response(state.events(since))

    async def api_subscribers(_request):
        snap = state.subscriber_snapshot()
        out = []
        for sub in bus.subscribers:
            meta = getattr(sub, "metadata", {}) or {}
            s = snap.get(sub.name, {})
            out.append({
                "name": sub.name,
                "type": sub.type,
                "transport": sub.transport,
                "address": getattr(sub, "address", "") or "",
                "events": list(getattr(sub, "events", []) or []),
                "vendor": meta.get("vendor") or "",
                "licence": meta.get("licence") or meta.get("license") or "",
                "ui_port": meta.get("ui_port"),
                "counts": s.get("counts", _empty_counts()),
                "avg_latency_ms": s.get("avg_latency_ms"),
                "last_response_ms": s.get("last_response_ms"),
                "last_seen": s.get("last_seen"),
                "last_decision": s.get("last_decision"),
            })
        return aiohttp.web.json_response(out)

    async def api_publishers(_request):
        return aiohttp.web.json_response(state.publishers())

    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/events", api_events)
    app.router.add_get("/api/subscribers", api_subscribers)
    app.router.add_get("/api/publishers", api_publishers)

