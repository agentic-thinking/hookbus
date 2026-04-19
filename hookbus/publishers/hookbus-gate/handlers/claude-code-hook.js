/**
 * Claude Code PreToolUse hook (also serves Auggie, same protocol).
 * Input:  stdin JSON: { tool_name, tool_input, session_id?, hook_event_name? }
 * Output: exit 0 = allow, exit 2 = deny (stderr = reason shown to agent).
 * Source label defaults to "claude-code"; override via HOOKBUS_SOURCE env var
 * (e.g. HOOKBUS_SOURCE=auggie).
 */
import { buildEnvelope, postEvent } from "../src/core.js";

function readStdin() {
  return new Promise((resolve) => {
    const chunks = [];
    process.stdin.on("data", (c) => chunks.push(c));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    if (process.stdin.isTTY) resolve("");
  });
}

export default async function handler() {
  const raw = await readStdin();
  let input = {};
  try { input = raw ? JSON.parse(raw) : {}; } catch { /* tolerate non-JSON */ }
  const envelope = buildEnvelope({
    source: process.env.HOOKBUS_SOURCE || "claude-code",
    toolName: input.tool_name,
    toolInput: input.tool_input,
    sessionId: input.session_id,
    eventType: input.hook_event_name || "PreToolUse",
  });
  const { decision, reason } = await postEvent(envelope);
  if (decision === "allow") return 0;
  process.stderr.write(`HookBus ${decision}: ${reason || "no reason given"}\n`);
  return 2;
}
