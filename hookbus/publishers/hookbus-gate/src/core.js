/**
 * hookbus-gate core: envelope + HTTP post + decision parsing.
 * Fail-closed: any error, timeout, or non-allow decision => BLOCK.
 */
import { request } from "node:http";
import { randomUUID } from "node:crypto";
import { hostname } from "node:os";

export const TOKEN = process.env.HOOKBUS_TOKEN || "";
export const BUS_URL = process.env.HOOKBUS_URL || "http://localhost:18800/event";
export const TIMEOUT_MS = parseInt(process.env.HOOKBUS_TIMEOUT_MS || "60000", 10);

export function buildEnvelope({ source, toolName, toolInput, sessionId, eventType = "PreToolUse" }) {
  return {
    event_id: randomUUID(),
    event_type: eventType,
    timestamp: new Date().toISOString(),
    source,
    session_id: sessionId || `${source}-${hostname()}-${process.pid}`,
    tool_name: toolName || "",
    tool_input: (typeof toolInput === "object" && toolInput !== null) ? toolInput : { value: toolInput },
    metadata: { publisher: "hookbus-gate", host: hostname() },
  };
}

export function postEvent(envelope) {
  return new Promise((resolve) => {
    let url;
    try { url = new URL(BUS_URL); }
    catch (e) { resolve({ decision: "deny", reason: `Invalid HOOKBUS_URL: ${e.message}` }); return; }
    const body = JSON.stringify(envelope);
    const req = request({
      hostname: url.hostname,
      port: url.port || (url.protocol === "https:" ? 443 : 80),
      path: url.pathname + (url.search || ""),
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
          const d = JSON.parse(buf);
          resolve({ decision: d.decision || "deny", reason: d.reason || "", raw: d });
        } catch {
          resolve({ decision: "deny", reason: `Non-JSON response (HTTP ${res.statusCode})` });
        }
      });
    });
    req.on("error", (e) => resolve({ decision: "deny", reason: `HookBus unreachable: ${e.message}` }));
    req.on("timeout", () => { req.destroy(); resolve({ decision: "deny", reason: "HookBus timeout (fail-closed)" }); });
    req.write(body);
    req.end();
  });
}
