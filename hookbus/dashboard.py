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
        self._events.appendleft({
            "id": self._next_id,
            "ts": ts,
            "source": source,
            "tool_name": getattr(event, "tool_name", "") or "",
            "event_type": getattr(event, "event_type", "") or "",
            "decision": decision_str,
            "reason": (reason or "")[:240],
            "latency_ms": round(latency_ms, 1),
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
<title>HookBus™ Light</title>
<style>
:root{--bg:#0b0d10;--panel:#11151a;--line:#1d232b;--text:#e6edf3;--dim:#8b949e;
      --green:#3fb950;--red:#f85149;--amber:#d29922;--blue:#58a6ff;--mono:'SF Mono',Menlo,Consolas,monospace;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:14px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
header{padding:14px 22px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;background:var(--panel)}
.brand{font-family:var(--mono);font-weight:700;letter-spacing:1px;color:var(--green)}
.brand span{color:var(--dim);font-weight:400;font-size:.78rem;margin-left:8px}
.licence{font-family:var(--mono);font-size:.72rem;color:var(--dim)}
.licence a{color:var(--blue);text-decoration:none}
.grid{display:grid;grid-template-columns:260px 340px 1fr;gap:14px;padding:14px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:12px}
.panel h2{font-size:.72rem;font-family:var(--mono);text-transform:uppercase;color:var(--dim);letter-spacing:1.5px;margin-bottom:10px}
.kpi{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line);font-family:var(--mono);font-size:.85rem}
.kpi:last-child{border-bottom:0}
.kpi b{font-weight:600}
.kpi .v{color:var(--text)}
.kpi .v.allow{color:var(--green)} .kpi .v.deny{color:var(--red)} .kpi .v.ask{color:var(--amber)}
.sub{padding:10px 0;border-bottom:1px solid var(--line);cursor:pointer}
.sub:last-child{border-bottom:0}
.sub:hover{background:rgba(88,166,255,0.04)}
.sub.active{background:rgba(88,166,255,0.08);border-left:2px solid var(--blue);margin-left:-12px;padding-left:10px}
.sub-name{font-family:var(--mono);font-size:.86rem;display:flex;justify-content:space-between;align-items:center}
.sub-name .tag{color:var(--dim);font-size:.7rem;font-weight:400}
.sub-meta{color:var(--dim);font-size:.7rem;font-family:var(--mono);margin-top:3px;display:flex;justify-content:space-between;gap:6px;flex-wrap:wrap}
.sub-meta .vendor{color:var(--blue)}
.sub-counts{display:flex;gap:8px;margin-top:6px;font-family:var(--mono);font-size:.72rem}
.sub-counts span{color:var(--dim)}
.sub-counts .n{font-weight:600}
.sub-counts .n.allow{color:var(--green)} .sub-counts .n.deny{color:var(--red)} .sub-counts .n.ask{color:var(--amber)}
.sub-action-row{margin-top:6px;font-family:var(--mono);font-size:.7rem}
.sub-action{color:var(--dim)}
.sub-action.ui{color:var(--blue);text-decoration:none}
.sub-action.ui:hover{text-decoration:underline}
.sub.has-ui .sub-name{color:var(--text)}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;background:var(--dim);margin-right:6px}
.dot.live{background:var(--green);box-shadow:0 0 6px var(--green)}
.dot.silent{background:var(--dim)}
.empty-hint{margin-top:12px;padding:10px;background:rgba(88,166,255,0.05);border-left:2px solid var(--blue);font-size:.72rem;color:var(--dim);font-family:var(--mono);line-height:1.5}
.empty-hint code{color:var(--text);background:var(--bg);padding:1px 5px;border-radius:3px}
.events{max-height:calc(100vh - 110px);overflow-y:auto}
.events-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.events-head h2{margin-bottom:0}
.filter-chip{font-family:var(--mono);font-size:.7rem;padding:2px 8px;background:var(--blue);color:var(--bg);border-radius:10px;cursor:pointer;display:none}
.filter-chip.on{display:inline-block}
.filter-chip:hover{opacity:.8}
.events table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:.78rem}
.events th{text-align:left;padding:8px 10px;color:var(--dim);text-transform:uppercase;font-size:.65rem;letter-spacing:1px;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--panel)}
.events td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
.events tr.new td{animation:flash 1.2s ease-out}
.ev-type{color:var(--dim);font-style:italic;opacity:.75}
.events tr.filtered{display:none}
@keyframes flash{from{background:#1d3a23}to{background:transparent}}
.d-allow{color:var(--green);font-weight:600}
.d-deny{color:var(--red);font-weight:600}
.d-ask{color:var(--amber);font-weight:600}
.empty{padding:20px;text-align:center;color:var(--dim);font-style:italic}
footer{padding:10px 22px;border-top:1px solid var(--line);font-family:var(--mono);font-size:.68rem;color:var(--dim);display:flex;justify-content:space-between}
footer a{color:var(--blue);text-decoration:none}
</style>
</head>
<body>
<header>
  <div class="brand">HOOKBUS™ <span>Light Edition</span></div>
  <div class="licence"><a href="https://agenticthinking.uk" target="_blank">agenticthinking.uk</a></div>
</header>
<div class="grid">
  <div class="panel">
    <h2>Bus Stats</h2>
    <div class="kpi"><b>Total</b><span class="v" id="s-total">0</span></div>
    <div class="kpi"><b>Allow</b><span class="v allow" id="s-allow">0</span></div>
    <div class="kpi"><b>Deny</b><span class="v deny" id="s-deny">0</span></div>
    <div class="kpi"><b>Ask</b><span class="v ask" id="s-ask">0</span></div>
    <div class="kpi"><b>Events / min</b><span class="v" id="s-epm">0</span></div>
    <div class="kpi"><b>Uptime</b><span class="v" id="s-up">0s</span></div>
  </div>
  <div class="panel">
    <h2>Connected Subscribers <span style="color:var(--dim);font-weight:400" id="sub-count"></span></h2>
    <div id="sub-list"><div class="empty">no subscribers connected</div></div>
    <div class="empty-hint">
      Subscribers are external plug-ins. To <strong>add or remove</strong> one, edit
      <code>subscribers.yaml</code> and restart the bus:
      <code>docker compose restart hookbus</code>.
      Hot-reload without restart is a HookBus Enterprise feature
      (<a href="mailto:hello@agenticthinking.uk?subject=HookBus%20Enterprise" style="color:var(--blue)">contact us</a>).
      See <a href="https://github.com/agentic-thinking/hookbus/blob/main/HOOKBUS_SPEC.md" style="color:var(--blue)">HOOKBUS_SPEC.md</a>
      for the envelope protocol, or
      <a href="https://github.com/agentic-thinking/hookbus/discussions" style="color:var(--blue)">join the community</a>
      to share a subscriber you have built.
    </div>
  </div>
  <div class="panel events">
    <div class="events-head">
      <h2>Live Events <span style="color:var(--dim);font-weight:400" id="ev-count"></span></h2>
      <span class="filter-chip" id="filter-chip">filter: <span id="filter-name"></span> &times;</span>
    </div>
    <table><thead><tr>
      <th>Time</th><th>Source</th><th>Tool</th><th>Decision</th><th>Latency</th><th>Reason</th>
    </tr></thead><tbody id="ev-body">
      <tr><td colspan="6" class="empty">waiting for events...</td></tr>
    </tbody></table>
  </div>
</div>
<footer>
  <span>HookBus™ v__VERSION__ &middot; Apache 2.0 &middot; &copy; 2026 Agentic Thinking Ltd</span>
  <span>HookBus&trade;. The agentic infrastructure.</span>
</footer>
<script>
let lastId = 0;
let activeFilter = null;
const fmtTime = ts => new Date(ts*1000).toLocaleTimeString('en-GB',{hour12:false}) + '.' + String(Math.floor((ts%1)*1000)).padStart(3,'0');
const fmtUp = s => { if(s<60)return s+'s'; if(s<3600)return Math.floor(s/60)+'m '+(s%60)+'s'; return Math.floor(s/3600)+'h '+Math.floor((s%3600)/60)+'m'; };
const esc = s => String(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

function applyFilter(){
  const chip = document.getElementById('filter-chip');
  const nameEl = document.getElementById('filter-name');
  if(activeFilter){ chip.classList.add('on'); nameEl.textContent = activeFilter; }
  else{ chip.classList.remove('on'); }
  document.querySelectorAll('#sub-list .sub').forEach(el=>{
    el.classList.toggle('active', el.dataset.name === activeFilter);
  });
  document.querySelectorAll('#ev-body tr').forEach(tr=>{
    if(!activeFilter){ tr.classList.remove('filtered'); return; }
    const reason = tr.dataset.reason || '';
    tr.classList.toggle('filtered', !reason.includes('['+activeFilter+']'));
  });
}

document.getElementById('filter-chip').addEventListener('click', ()=>{ activeFilter = null; applyFilter(); });

async function tickStats(){
  try{
    const r = await fetch('/api/stats'); const d = await r.json();
    document.getElementById('s-total').textContent = d.total;
    document.getElementById('s-allow').textContent = d.allow;
    document.getElementById('s-deny').textContent = d.deny;
    document.getElementById('s-ask').textContent = d.ask;
    document.getElementById('s-epm').textContent = d.events_per_min;
    document.getElementById('s-up').textContent = fmtUp(d.uptime_s);
  }catch(e){}
}
async function tickSubs(){
  try{
    const r = await fetch('/api/subscribers'); const d = await r.json();
    const now = Date.now()/1000;
    document.getElementById('sub-count').textContent = '(' + d.length + ')';
    const list = document.getElementById('sub-list');
    if(!d.length){ list.innerHTML='<div class="empty">no subscribers connected</div>'; return; }
    list.innerHTML = d.map(s=>{
      const live = s.last_seen && (now - s.last_seen) < 30;
      const lat = s.avg_latency_ms != null ? s.avg_latency_ms.toFixed(1)+'ms avg' : '-';
      const vendor = s.vendor ? `<span class="vendor">${esc(s.vendor)}</span>` : '<span style="color:var(--dim)">unknown vendor</span>';
      const licence = s.licence ? esc(s.licence) : '';
      const vl = licence ? `${vendor} &middot; ${licence}` : vendor;
      const c = s.counts || {total:0,allow:0,deny:0,ask:0};
      const hasUi = s.ui_port != null && s.ui_port !== '';
      const uiUrl = hasUi ? `http://${window.location.hostname}:${s.ui_port}` : '';
      const action = hasUi
        ? `<a class="sub-action ui" href="${uiUrl}" target="_blank" rel="noopener">open dashboard &rarr;</a>`
        : `<span class="sub-action filter">click to filter events</span>`;
      return `<div class="sub ${hasUi?'has-ui':''}" data-name="${esc(s.name)}" data-has-ui="${hasUi?1:0}">
        <div class="sub-name"><span><span class="dot ${live?'live':'silent'}"></span>${esc(s.name)}</span><span class="tag">${esc(s.type)} &middot; ${esc(s.transport)}</span></div>
        <div class="sub-meta"><span>${vl}</span><span>${esc(lat)}</span></div>
        <div class="sub-counts"><span><span class="n">${c.total}</span> events</span><span><span class="n allow">${c.allow}</span> allow</span><span><span class="n deny">${c.deny}</span> deny</span><span><span class="n ask">${c.ask}</span> ask</span></div>
        <div class="sub-action-row">${action}</div>
      </div>`;
    }).join('');
    document.querySelectorAll('#sub-list .sub').forEach(el=>{
      el.addEventListener('click', (ev)=>{
        // Allow real link clicks to pass through (new tab to subscriber dashboard)
        if(ev.target.tagName === 'A') return;
        if(el.dataset.hasUi === '1'){
          const a = el.querySelector('a.sub-action');
          if(a) window.open(a.href, '_blank', 'noopener');
          return;
        }
        activeFilter = (activeFilter === el.dataset.name) ? null : el.dataset.name;
        applyFilter();
      });
    });
    applyFilter();
  }catch(e){}
}
async function tickEvents(){
  try{
    const r = await fetch('/api/events?since=' + lastId); const arr = await r.json();
    if(!arr.length) return;
    arr.forEach(e=>{ if(e.id > lastId) lastId = e.id; });
    const body = document.getElementById('ev-body');
    if(body.querySelector('.empty')) body.innerHTML='';
    // Backend returns newest-first; iterate in reverse so insertBefore keeps chronological order within a poll batch.
    for(let i = arr.length - 1; i >= 0; i--){
      const e = arr[i];
      const tr = document.createElement('tr');
      tr.className = 'new';
      tr.dataset.reason = e.reason || '';
      const toolCell = e.tool_name
        ? esc(e.tool_name)
        : `<span class="ev-type">${esc(e.event_type || "")}</span>`;
      tr.innerHTML = `<td>${fmtTime(e.ts)}</td><td>${esc(e.source)}</td><td>${toolCell}</td>
        <td class="d-${esc(e.decision)}">${esc(e.decision.toUpperCase())}</td>
        <td>${e.latency_ms||0}ms</td><td>${esc(e.reason)}</td>`;
      body.insertBefore(tr, body.firstChild);
    }
    while(body.children.length > 200) body.removeChild(body.lastChild);
    document.getElementById('ev-count').textContent = '(showing ' + body.children.length + ')';
    applyFilter();
  }catch(e){}
}
tickStats(); tickSubs(); tickEvents();
setInterval(tickStats, 2000);
setInterval(tickSubs, 5000);
setInterval(tickEvents, 1000);
</script>
</body>
</html>
"""
