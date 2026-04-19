/**
 * OpenAI Codex CLI hook handler.
 *
 * Codex implements a Claude-Code-style hook runtime (SessionStart,
 * UserPromptSubmit, PreToolUse, PostToolUse, Stop) but with stricter output
 * rules per event. Most importantly, Stop forbids hookSpecificOutput and
 * requires JSON on stdout (plain text is invalid).
 *
 * Input:  stdin JSON from Codex with {hook_event_name, session_id, cwd,
 *         model, plus event-specific fields (tool_name, tool_input,
 *         prompt, last_assistant_message, source for SessionStart, ...)}.
 * Output: per-event-type JSON on stdout; exit 0 allow, exit 2 deny.
 *
 * Source label defaults to "codex"; override via HOOKBUS_SOURCE env var.
 *
 * Register in ~/.codex/hooks.json, e.g.:
 *   {
 *     "SessionStart":      [{"command": "node /opt/hookbus-gate/src/index.js --mode=codex-hook"}],
 *     "UserPromptSubmit":  [{"command": "node /opt/hookbus-gate/src/index.js --mode=codex-hook"}],
 *     "PreToolUse":        [{"command": "node /opt/hookbus-gate/src/index.js --mode=codex-hook"}],
 *     "PostToolUse":       [{"command": "node /opt/hookbus-gate/src/index.js --mode=codex-hook"}],
 *     "Stop":              [{"command": "node /opt/hookbus-gate/src/index.js --mode=codex-hook"}]
 *   }
 *
 * Codex's hooks runtime is gated behind features.codex_hooks=true (set
 * in ~/.codex/config.toml or via `-c features.codex_hooks=true`).
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

function emit(obj) {
  process.stdout.write(JSON.stringify(obj));
}

export default async function handler() {
  const raw = await readStdin();
  let input = {};
  try { input = raw ? JSON.parse(raw) : {}; } catch { /* tolerate */ }

  const hook = input.hook_event_name || "";
  const envelope = buildEnvelope({
    source: process.env.HOOKBUS_SOURCE || "codex",
    toolName: input.tool_name || "",
    toolInput: input.tool_input || (hook === "UserPromptSubmit" ? { prompt: input.prompt || "" } : {}),
    sessionId: input.session_id,
    eventType: hook || "PreToolUse",
  });

  let decision = "allow";
  let reason = "";
  try {
    const result = await postEvent(envelope);
    decision = result.decision || "allow";
    reason = result.reason || "";
  } catch {
    // Bus unreachable: fail open so Codex is never bricked.
    emit({});
    return 0;
  }

  // Deny - event-type specific shape
  if (decision === "deny") {
    if (hook === "PreToolUse") {
      emit({
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "deny",
          permissionDecisionReason: reason,
        },
      });
    } else if (hook === "Stop") {
      // Stop schema forbids hookSpecificOutput.
      emit({ decision: "block", reason });
    } else {
      // SessionStart / PostToolUse / UserPromptSubmit: surface as systemMessage.
      emit({ systemMessage: reason });
    }
    return 2;
  }

  // Ask (PreToolUse semantics only)
  if (decision === "ask") {
    if (hook === "PreToolUse") {
      emit({
        hookSpecificOutput: {
          hookEventName: "PreToolUse",
          permissionDecision: "ask",
          additionalContext: reason,
        },
      });
    } else {
      emit({});
    }
    return 0;
  }

  // Allow - event-type specific
  if (hook === "PreToolUse" || hook === "PostToolUse") {
    if (reason) {
      emit({
        hookSpecificOutput: { hookEventName: hook, additionalContext: reason },
      });
    } else {
      emit({});
    }
  } else if (hook === "UserPromptSubmit") {
    if (reason) {
      emit({
        hookSpecificOutput: { hookEventName: "UserPromptSubmit", additionalContext: reason },
      });
    } else {
      emit({});
    }
  } else if (hook === "Stop" || hook === "SessionStart") {
    // Stop + SessionStart: no hookSpecificOutput allowed on allow.
    if (reason) {
      emit({ systemMessage: reason });
    } else {
      emit({});
    }
  } else {
    emit({});
  }
  return 0;
}
