/**
 * OpenClaw / Hermes plugin handler. Unlike CLI-hook modes, this one runs
 * INSIDE the host process via the plugin API (register() default export).
 * Use by dropping this file into the agent's plugin/extensions dir, not
 * by invoking hookbus-gate on the CLI.
 *
 * Source label defaults to "openclaw"; set HOOKBUS_SOURCE=hermes to reuse
 * for Hermes once its plugin ABI is confirmed.
 */
import { buildEnvelope, postEvent } from "../src/core.js";

export default function register(api) {
  api.on("before_tool_call", async (event) => {
    const envelope = buildEnvelope({
      source: process.env.HOOKBUS_SOURCE || "openclaw",
      toolName: event.toolName,
      toolInput: event.params,
    });
    const { decision, reason } = await postEvent(envelope);
    if (decision === "allow") return;
    const err = new Error(`HookBus ${decision}: ${reason || "no reason given"}`);
    err.code = "HOOKBUS_BLOCKED";
    throw err;
  });
}
