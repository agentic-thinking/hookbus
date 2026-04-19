#!/usr/bin/env node
/**
 * hookbus-gate: universal HookBus publisher.
 * One binary, N agent protocol skins. Dispatches on --mode=<handler>
 * or HOOKBUS_GATE_MODE env var.
 *
 * Modes:
 *   claude-code-hook   Claude Code PreToolUse hook (stdin JSON, exit 0/2)
 *   amp-delegate       Amp delegate (AGENT_TOOL_NAME env + stdin, exit 0/1/2)
 *   shell-wrapper      $SHELL replacement (used by bob, auggie, any $SHELL-honouring agent)
 *   openclaw-plugin    OpenClaw before_tool_call plugin (api.on handler)
 *   auggie-shell       Alias for shell-wrapper with source="auggie"
 *   hermes             Nous Research Hermes Agent (protocol TBD)
 */
import { readFileSync } from "node:fs";

async function main() {
  const argv = process.argv.slice(2);
  let mode = process.env.HOOKBUS_GATE_MODE || "";
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith("--mode=")) { mode = argv[i].split("=")[1]; argv.splice(i, 1); break; }
    if (argv[i] === "--mode" && argv[i + 1]) { mode = argv[i + 1]; argv.splice(i, 2); break; }
  }
  if (!mode) {
    console.error("hookbus-gate: --mode=<handler> required. Available: claude-code-hook, codex-hook, gemini-beforeagent, amp-delegate, shell-wrapper, openclaw-plugin, auggie-shell, hermes");
    process.exit(2);
  }
  const modPath = `../handlers/${mode}.js`;
  let handler;
  try {
    handler = (await import(new URL(modPath, import.meta.url).href)).default;
  } catch (e) {
    console.error(`hookbus-gate: unknown mode "${mode}": ${e.message}`);
    process.exit(2);
  }
  if (typeof handler !== "function") {
    console.error(`hookbus-gate: handler "${mode}" has no default export`);
    process.exit(2);
  }
  try {
    const code = await handler(argv);
    if (typeof code === "number") process.exit(code);
  } catch (e) {
    console.error(`hookbus-gate: handler "${mode}" crashed: ${e.message}`);
    process.exit(2);
  }
}

main();
