/**
 * SHELL replacement. Serves Bob / Gemini CLI / Auggie / any agent that
 * invokes $SHELL -c "<cmd>" for run_shell_command.
 *
 * Invocation:  hookbus-gate --mode=shell-wrapper -c "<cmd>"
 * (or: set as $SHELL, agent calls it as SHELL -c "<cmd>")
 *
 * Source defaults to "shell"; override via HOOKBUS_SOURCE.
 */
import { spawn } from "node:child_process";
import { buildEnvelope, postEvent } from "../src/core.js";

export default async function handler(argv) {
  // Normalise: find the command after "-c"
  let cmd = "";
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "-c" && argv[i + 1] != null) { cmd = argv[i + 1]; break; }
  }
  if (!cmd) {
    // No -c <cmd> => interactive shell requested. Not our use case; pass through.
    const sh = spawn(process.env.HOOKBUS_REAL_SHELL || "/bin/bash", argv, { stdio: "inherit" });
    return new Promise((resolve) => sh.on("exit", (code) => resolve(code ?? 0)));
  }
  const envelope = buildEnvelope({
    source: process.env.HOOKBUS_SOURCE || "shell",
    toolName: "run_shell_command",
    toolInput: { command: cmd, cwd: process.cwd() },
  });
  const { decision, reason } = await postEvent(envelope);
  if (decision !== "allow") {
    process.stderr.write(`[hookbus-gate] ${decision}: ${reason || "no reason given"}\n`);
    return 1;
  }
  // Allowed: execute via real shell
  const realShell = process.env.HOOKBUS_REAL_SHELL || "/bin/bash";
  return new Promise((resolve) => {
    const child = spawn(realShell, ["-c", cmd], { stdio: "inherit" });
    child.on("exit", (code) => resolve(code ?? 0));
    child.on("error", (e) => { process.stderr.write(String(e) + "\n"); resolve(127); });
  });
}
