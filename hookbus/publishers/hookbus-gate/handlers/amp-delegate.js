/**
 * Amp delegate.
 * Input:  AGENT_TOOL_NAME env var + stdin JSON (tool arguments)
 * Output: exit 0 = allow, exit 1 = ask user, exit 2 = reject (stderr = reason)
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
  let toolInput = {};
  try { toolInput = raw ? JSON.parse(raw) : {}; } catch { toolInput = { raw }; }
  const envelope = buildEnvelope({
    source: "amp",
    toolName: process.env.AGENT_TOOL_NAME || "unknown",
    toolInput,
    sessionId: process.env.AMP_SESSION_ID,
  });
  const { decision, reason } = await postEvent(envelope);
  if (decision === "allow") return 0;
  if (decision === "ask")   { process.stderr.write(reason || "approval required\n"); return 1; }
  process.stderr.write(`HookBus deny: ${reason || "no reason given"}\n`);
  return 2;
}
