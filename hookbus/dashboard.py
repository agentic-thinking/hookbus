"""HookBus Light dashboard.

Read-only event monitor bundled into HookBus Light. Same HTTP server
as the bus (port 18800 by default). Routes:

    GET /                -> HTML dashboard (single-page, polls /api/*)
    GET /api/stats       -> {total, allow, deny, ask, events_per_min, uptime_s}
    GET /api/subscribers -> [{name, type, transport, vendor, licence, counts, last_*}]
    GET /api/events      -> [{id, ts, source, tool_name, event_type, decision, reason, latency_ms}]
                            optional ?since=<id> for incremental polling

Subscribers are external plug-ins, configured via subscribers.yaml,
running as separate processes or containers. The bus connects to them
by name. To add one: edit ~/.hookbus/subscribers.yaml and SIGHUP the
bus. See HOOKBUS_SPEC.md in the public repo for the envelope protocol.
"""

from __future__ import annotations

import collections
import re
import time
from typing import Any, Dict, Optional

import aiohttp.web

__all__ = ["DashboardState", "register_dashboard_routes", "HTML"]

_RING_SIZE = 500

# Regex to pull subscriber names from reason strings. Reason strings
# carry a "[subscriber-name] ..." prefix emitted by each subscriber.
_REASON_NAME = re.compile(r"\[([a-z0-9][a-z0-9_\-]{0,63})\]")


def _empty_counts() -> Dict[str, int]:
    return {"total": 0, "allow": 0, "deny": 0, "ask": 0}


class DashboardState:
    """In-memory event + stats state for the Light dashboard."""

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
        """Record a bus decision for the live dashboard, update rolling counters, per-subscriber stats, and the recent-events ring buffer."""
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
        """Return a JSON-serialisable snapshot of bus stats (decision counters, events-per-minute, uptime) for the dashboard API."""
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


def register_dashboard_routes(app: aiohttp.web.Application, bus) -> None:
    """Attach Light dashboard routes to an existing aiohttp app."""

    state = bus.dashboard

    async def index(_request):
        from hookbus import __version__ as _ver
        return aiohttp.web.Response(text=HTML.replace("__VERSION__", _ver), content_type="text/html")

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

    app.router.add_get("/", index)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/events", api_events)
    app.router.add_get("/api/subscribers", api_subscribers)
    app.router.add_get("/api/publishers", api_publishers)


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HookBus Light</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--ink:#15191f;--muted:#68707d;--line:#d8dde5;--soft:#eef1f5;
--green:#18864b;--red:#c62828;--amber:#b77905;--blue:#1f5eff;--mono:'SF Mono',Menlo,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.top{height:68px;background:var(--panel);border-bottom:1px solid var(--line);display:flex;align-items:center;gap:28px;padding:0 28px}
.brand{font-size:28px;font-weight:760;letter-spacing:-.02em}.pill{display:flex;align-items:center;gap:16px;border:1px solid var(--line);border-radius:8px;padding:9px 12px;background:#fafbfc}
.dot{width:9px;height:9px;border-radius:999px;background:var(--green);display:inline-block;margin-right:7px}.muted{color:var(--muted)}.mono{font-family:var(--mono)}
.links{margin-left:auto;display:flex;gap:16px;font-family:var(--mono);font-size:12px}
.metrics{margin:18px 24px 12px;border:1px solid var(--line);background:var(--panel);border-radius:8px;display:grid;grid-template-columns:repeat(6,1fr)}
.metric{padding:13px 16px;border-right:1px solid var(--line)}.metric:last-child{border-right:0}.metric b{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:3px}.metric span{font-family:var(--mono);font-weight:700}
.toolbar{display:flex;gap:12px;align-items:center;padding:0 24px 12px}.toolbar input,.toolbar select{border:1px solid var(--line);border-radius:6px;background:#fff;padding:8px 10px;font:inherit;min-width:150px}.toolbar input{min-width:280px}.toolbar button{border:1px solid var(--line);background:#fff;border-radius:6px;padding:8px 10px;cursor:pointer}
.shell{height:calc(100vh - 184px);min-height:480px;padding:0 24px 18px;display:grid;grid-template-columns:minmax(700px,1fr) 380px;gap:16px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}.panel-head{height:44px;display:flex;align-items:center;justify-content:space-between;padding:0 14px;border-bottom:1px solid var(--line);font-weight:700}.panel-head small{font-weight:500;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}th{background:#2b3036;color:#fff;text-align:left;padding:10px 12px;font-size:11px;text-transform:uppercase;letter-spacing:.08em;position:sticky;top:0}td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}tbody tr{cursor:pointer}tbody tr:hover{background:#f1f6ff}tbody tr.selected{background:#e8f2ff}
.badge{display:inline-block;min-width:58px;text-align:center;border-radius:6px;padding:5px 8px;font-weight:800;font-size:12px}.allow{background:#dff5e8;color:var(--green)}.deny{background:#ffe4e4;color:var(--red)}.ask{background:#fff2cc;color:var(--amber)}.other{background:#e9ecef;color:#4d5561}
.reason{max-width:440px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.empty{padding:28px;text-align:center;color:var(--muted)}
.inspector{display:flex;flex-direction:column}.section{padding:14px;border-bottom:1px solid var(--line)}.section h3{margin:0 0 10px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.kv{display:grid;grid-template-columns:118px 1fr;gap:6px 10px;font-size:13px}.kv span:nth-child(odd){color:var(--muted)}pre{margin:0;background:#0f141a;color:#d7e1ed;border-radius:6px;padding:10px;overflow:auto;max-height:210px;font:12px/1.45 var(--mono)}
.sub-row{border:1px solid var(--line);border-radius:6px;padding:8px;margin-bottom:8px}.sub-row b{font-family:var(--mono)}
.foot{padding:10px 24px;border-top:1px solid var(--line);font-size:12px;color:var(--muted);display:flex;justify-content:space-between;background:#fff}.foot span:last-child{max-width:720px;text-align:right}
@media(max-width:1100px){.shell{grid-template-columns:1fr;height:auto}.metrics{grid-template-columns:repeat(2,1fr)}.metric{border-bottom:1px solid var(--line)}.top{height:auto;flex-wrap:wrap;padding:16px 20px}.links{margin-left:0}}
</style>
</head>
<body>
<div class="top">
  <div class="brand">HookBus Light</div>
  <div class="pill"><span><i class="dot"></i>Bus online</span><span>Profile: <b>Light</b></span><span>Endpoint: <b class="mono" id="endpoint">localhost:18800</b></span><button id="copy-endpoint">Copy</button></div>
  <div class="links"><a href="https://agenticthinking.uk" target="_blank" rel="noopener">agenticthinking.uk</a><a href="https://github.com/agentic-thinking/hookbus" target="_blank" rel="noopener">GitHub</a><a href="https://github.com/agentic-thinking/cre-agentprotect" target="_blank" rel="noopener">CRE-AgentProtect</a></div>
</div>
<div class="metrics">
  <div class="metric"><b>Publishers</b><span id="m-pubs">0</span></div>
  <div class="metric"><b>Subscribers</b><span id="m-subs">0</span></div>
  <div class="metric"><b>Events / min</b><span id="m-epm">0</span></div>
  <div class="metric"><b>Allow</b><span class="allow-text" id="m-allow">0</span></div>
  <div class="metric"><b>Deny / Ask</b><span><span id="m-deny">0</span> / <span id="m-ask">0</span></span></div>
  <div class="metric"><b>Uptime</b><span id="m-up">0s</span></div>
</div>
<div class="toolbar">
  <input id="q" placeholder="Filter events">
  <select id="decision"><option value="">All decisions</option><option value="allow">Allow</option><option value="deny">Deny</option><option value="ask">Ask</option></select>
  <select id="source"><option value="">All sources</option></select>
  <button id="refresh">Refresh</button>
</div>
<div class="shell">
  <div class="panel">
    <div class="panel-head"><span>Event stream</span><small id="event-count">waiting for events</small></div>
    <table><thead><tr><th>Time</th><th>Source</th><th>Event</th><th>Tool</th><th>Decision</th><th>Subscriber</th><th>Reason</th><th>Latency</th></tr></thead><tbody id="events"><tr><td colspan="8" class="empty">Waiting for events. Install a publisher or POST a test envelope.</td></tr></tbody></table>
  </div>
  <aside class="panel inspector">
    <div class="panel-head"><span>Event details</span><small id="selected-label">select a row</small></div>
    <div id="details" class="section"><div class="empty">Click an event to inspect the envelope, tool input, and subscriber response.</div></div>
    <div class="section">
      <h3>Subscribers</h3>
      <div id="subs"></div>
    </div>
    <div class="section">
      <h3>Policy configuration</h3>
      <p class="muted">To update AGT policy coverage in Light, update the AGT policy YAML used by CRE-AgentProtect, then restart the subscriber:</p>
      <pre>docker compose restart cre-agentprotect</pre>
      <p><a href="https://github.com/microsoft/agent-governance-toolkit/tree/main/examples/policies" target="_blank" rel="noopener">Microsoft AGT example policy YAML files</a></p>
    </div>
  </aside>
</div>
<div class="foot">
  <span>HookBus v__VERSION__ · Apache 2.0 open source · evaluation, development, and internal pilots</span>
  <span>Enterprise adds advanced subscribers, compliance workflows, policy management, audit exports, RBAC, and support.</span>
</div>
<script>
let lastId=0, events=[], selected=null, subs=[], pubs={};
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const time=ts=>new Date(ts*1000).toLocaleTimeString('en-GB',{hour12:false});
const up=s=>s<60?s+'s':s<3600?Math.floor(s/60)+'m '+s%60+'s':Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m';
const badge=d=>`<span class="badge ${['allow','deny','ask'].includes(d)?d:'other'}">${esc(String(d||'').toUpperCase())}</span>`;
const subFromReason=r=>{const m=String(r||'').match(/\[([a-z0-9][a-z0-9_-]{0,63})\]/);return m?m[1]:'-';};
const pretty=o=>esc(JSON.stringify(o??{},null,2));
function filtered(){
  const q=$('q').value.toLowerCase(), d=$('decision').value, s=$('source').value;
  return events.filter(e=>(!d||e.decision===d)&&(!s||e.source===s)&&(!q||JSON.stringify(e).toLowerCase().includes(q)));
}
function renderEvents(){
  const rows=filtered();
  $('event-count').textContent=rows.length?`showing ${rows.length}`:'waiting for events';
  if(!rows.length){$('events').innerHTML='<tr><td colspan="8" class="empty">No matching events.</td></tr>';return}
  $('events').innerHTML=rows.map(e=>`<tr data-id="${e.id}" class="${selected&&selected.id===e.id?'selected':''}">
    <td class="mono">${time(e.ts)}</td><td>${esc(e.source||'-')}</td><td>${esc(e.event_type||'-')}</td><td>${esc(e.tool_name||'-')}</td><td>${badge(e.decision)}</td><td>${esc(subFromReason(e.reason))}</td><td class="reason">${esc(e.reason)}</td><td class="mono">${esc(e.latency_ms)}ms</td>
  </tr>`).join('');
  document.querySelectorAll('#events tr[data-id]').forEach(tr=>tr.onclick=()=>selectEvent(Number(tr.dataset.id)));
}
function renderSources(){
  const current=$('source').value;
  const values=[...new Set(events.map(e=>e.source).filter(Boolean))].sort();
  $('source').innerHTML='<option value="">All sources</option>'+values.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');
  if(values.includes(current))$('source').value=current;
}
function renderSubs(){
  $('m-subs').textContent=subs.length;
  $('subs').innerHTML=subs.length?subs.map(s=>{
    const c=s.counts||{};
    return `<div class="sub-row"><b>${esc(s.name)}</b> <span class="muted">${esc(s.type)} · ${esc(s.transport)}</span><div class="muted">${esc((s.events||[]).join(', '))}</div><div>allow ${c.allow||0} · deny ${c.deny||0} · ask ${c.ask||0}</div></div>`;
  }).join(''):'<p class="muted">No subscribers configured.</p>';
}
function selectEvent(id){
  selected=events.find(e=>e.id===id)||null; renderEvents();
  if(!selected)return;
  $('selected-label').textContent='#'+selected.id;
  const rs=selected.subscriber_responses||[];
  $('details').innerHTML=`<div class="section"><h3>Summary</h3><div class="kv">
    <span>Event ID</span><b class="mono">${esc(selected.event_id||selected.id)}</b><span>Session</span><span class="mono">${esc(selected.session_id||'-')}</span><span>Correlation</span><span class="mono">${esc(selected.correlation_id||'-')}</span><span>Source</span><span>${esc(selected.source||'-')}</span><span>Event type</span><span>${esc(selected.event_type||'-')}</span><span>Decision</span><span>${badge(selected.decision)}</span><span>Latency</span><span class="mono">${esc(selected.latency_ms)}ms</span>
  </div></div><div class="section"><h3>Tool input</h3><pre>${pretty(selected.tool_input)}</pre></div><div class="section"><h3>Subscriber responses</h3>${rs.length?rs.map(r=>`<div class="sub-row"><b>${esc(r.subscriber)}</b> ${badge(r.decision)}<div>${esc(r.reason)}</div></div>`).join(''):`<div class="sub-row"><b>${esc(subFromReason(selected.reason))}</b> ${badge(selected.decision)}<div>${esc(selected.reason)}</div></div>`}</div><div class="section"><h3>Raw envelope</h3><pre>${pretty(selected)}</pre></div>`;
}
async function stats(){try{const d=await (await fetch('/api/stats')).json();$('m-epm').textContent=d.events_per_min;$('m-allow').textContent=d.allow;$('m-deny').textContent=d.deny;$('m-ask').textContent=d.ask;$('m-up').textContent=up(d.uptime_s)}catch(e){}}
async function getSubs(){try{subs=await (await fetch('/api/subscribers')).json();renderSubs()}catch(e){}}
async function getPubs(){try{pubs=await (await fetch('/api/publishers')).json();$('m-pubs').textContent=Object.keys(pubs).length}catch(e){}}
async function getEvents(){try{const arr=await (await fetch('/api/events?since='+lastId)).json();if(!arr.length)return;arr.forEach(e=>{if(e.id>lastId)lastId=e.id});events=[...arr.reverse(),...events].slice(0,300);if(!selected)selected=events[0];renderSources();renderEvents();if(selected)selectEvent(selected.id)}catch(e){}}
$('endpoint').textContent=location.host;$('copy-endpoint').onclick=()=>navigator.clipboard&&navigator.clipboard.writeText(location.origin+'/event');$('q').oninput=renderEvents;$('decision').onchange=renderEvents;$('source').onchange=renderEvents;$('refresh').onclick=()=>{stats();getSubs();getPubs();getEvents()};
stats();getSubs();getPubs();getEvents();setInterval(stats,2000);setInterval(getSubs,5000);setInterval(getPubs,5000);setInterval(getEvents,1000);
</script>
</body>
</html>
"""
