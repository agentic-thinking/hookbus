/**
 * Hermes Agent (Nous Research, MIT) handler, PLACEHOLDER.
 *
 * Until ABI is confirmed, this handler falls through to shell-wrapper mode
 * (covers run_shell_command only). Set HOOKBUS_SOURCE=hermes when invoking.
 */
import shellWrapper from "./shell-wrapper.js";

export default async function handler(argv) {
  process.env.HOOKBUS_SOURCE = process.env.HOOKBUS_SOURCE || "hermes";
  return shellWrapper(argv);
}
