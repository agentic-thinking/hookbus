/**
 * Gemini CLI BeforeAgent hook handler.
 * Input:  stdin JSON with {prompt, session_id, hook_event_name, cwd}
 * Output: stdout JSON with {hookSpecificOutput: {additionalContext: "..."}}
 * Semantics: Post UserPromptSubmit to HookBus, take the CRE "reason" string
 * (which contains injected KB context), return it as additionalContext so
 * Gemini's model sees the KB before planning.
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
  try { input = raw ? JSON.parse(raw) : {}; } catch { /* tolerate */ }
  const prompt = input.prompt || "";
  const envelope = buildEnvelope({
    source: process.env.HOOKBUS_SOURCE || "gemini-cli",
    toolName: "",
    toolInput: { prompt },
    sessionId: input.session_id,
    eventType: "UserPromptSubmit",
  });
  const { decision, reason } = await postEvent(envelope);
  if (decision === "deny") {
    process.stderr.write(`HookBus deny: ${reason || "blocked"}\n`);
    return 2;
  }
  // Inject the full CRE reason (KB + enforcement rules with PIN guidance)
  let ctx = (reason || "").trim();
  if (!ctx) return 0; // nothing to inject, allow silently
  const out = { hookSpecificOutput: { additionalContext: ctx } };
  process.stdout.write(JSON.stringify(out));
  return 0;
}
