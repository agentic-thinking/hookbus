/**
 * HookBus publisher for OpenClaw.
 * Forwards before_tool_call events over HTTP to the HookBus /event endpoint
 * and enforces the consolidated decision (allow / deny / ask).
 * Fail-closed: bus unreachable or non-allow => block.
 */
import { request } from "node:http";
import { randomUUID } from "node:crypto";
import { hostname } from "node:os";

const TOKEN = process.env.HOOKBUS_TOKEN || "";
const BUS_URL = process.env.HOOKBUS_URL || "http://localhost:18800/event";
const TIMEOUT_MS = parseInt(process.env.HOOKBUS_TIMEOUT_MS || "60000", 10);

function postEvent(envelope) {
  return new Promise((resolve) => {
    let url;
    try { url = new URL(BUS_URL); } catch (e) {
      resolve({ decision: "deny", reason: `Invalid HOOKBUS_URL: ${e.message}` });
      return;
    }
    const body = JSON.stringify(envelope);
    const req = request({
      hostname: url.hostname,
      port: url.port || 80,
      path: url.pathname,
      method: "POST",
      headers: (() => {
        const h = { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) };
        if (TOKEN) h["Authorization"] = `Bearer ${TOKEN}`;
        return h;
      })(),
      timeout: TIMEOUT_MS,
    }, (res) => {
      let buf = "";
      res.on("data", (c) => (buf += c));
      res.on("end", () => {
        try {
          const data = JSON.parse(buf);
          resolve({ decision: data.decision || "deny", reason: data.reason || "" });
        } catch {
          resolve({ decision: "deny", reason: "HookBus returned non-JSON (fail-closed)" });
        }
      });
    });
    req.on("error", (e) => resolve({ decision: "deny", reason: `HookBus unreachable: ${e.message}` }));
    req.on("timeout", () => { req.destroy(); resolve({ decision: "deny", reason: "HookBus timeout" }); });
    req.write(body);
    req.end();
  });
}

function buildEnvelope(toolName, toolInput) {
  return {
    event_id: randomUUID(),
    event_type: "PreToolUse",
    timestamp: new Date().toISOString().replace(/\.(\d{3})\d*Z$/, ".$1Z"),
    source: "openclaw",
    session_id: process.env.OPENCLAW_SESSION_ID || `openclaw-${hostname()}-${process.pid}`,
    tool_name: toolName || "unknown",
    tool_input: typeof toolInput === "object" && toolInput !== null ? toolInput : { value: toolInput },
    metadata: { publisher: "hookbus-openclaw-publisher", host: hostname() },
  };
}

export default function register(api) {
  api.on("before_tool_call", async (event) => {
    const envelope = buildEnvelope(event.toolName, event.params);
    const { decision, reason } = await postEvent(envelope);
    if (decision === "allow") return;
    const err = new Error(`HookBus ${decision}: ${reason || "no reason given"}`);
    err.code = "HOOKBUS_BLOCKED";
    throw err;
  });
}

// exported for tests
export { buildEnvelope, postEvent };
