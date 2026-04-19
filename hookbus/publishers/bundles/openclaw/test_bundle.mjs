// Automated test: spin up mock HookBus receiver, verify plugin POSTs correct envelope
// and honours allow / deny / bus-down (fail-closed).
import { createServer } from "node:http";
import assert from "node:assert/strict";

async function startMockBus(handler) {
  const srv = createServer((req, res) => {
    let buf = "";
    req.on("data", (c) => (buf += c));
    req.on("end", async () => {
      const envelope = JSON.parse(buf);
      const reply = await handler(envelope);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(reply));
    });
  });
  await new Promise((r) => srv.listen(0, "127.0.0.1", r));
  return { port: srv.address().port, close: () => new Promise((r) => srv.close(r)) };
}

async function loadPublisher(url) {
  process.env.HOOKBUS_URL = url;
  // fresh module each call
  const mod = await import(`./index.js?cachebust=${Math.random()}`);
  return mod;
}

async function testEnvelopeShape() {
  let captured;
  const bus = await startMockBus(async (env) => { captured = env; return { decision: "allow", reason: "" }; });
  try {
    const { buildEnvelope, postEvent } = await loadPublisher(`http://127.0.0.1:${bus.port}/event`);
    const env = buildEnvelope("Bash", { command: "echo hi" });
    await postEvent(env);
    assert.equal(captured.event_type, "PreToolUse");
    assert.equal(captured.source, "openclaw");
    assert.equal(captured.tool_name, "Bash");
    assert.deepEqual(captured.tool_input, { command: "echo hi" });
    assert.match(captured.event_id, /^[0-9a-f-]{36}$/);
    assert.ok(captured.metadata.publisher);
    console.log("OK envelope shape");
  } finally { await bus.close(); }
}

async function testAllowPath() {
  const bus = await startMockBus(async () => ({ decision: "allow", reason: "" }));
  try {
    const { postEvent, buildEnvelope } = await loadPublisher(`http://127.0.0.1:${bus.port}/event`);
    const r = await postEvent(buildEnvelope("Bash", {}));
    assert.equal(r.decision, "allow");
    console.log("OK allow path");
  } finally { await bus.close(); }
}

async function testDenyPath() {
  const bus = await startMockBus(async () => ({ decision: "deny", reason: "blocked by test" }));
  try {
    const { postEvent, buildEnvelope } = await loadPublisher(`http://127.0.0.1:${bus.port}/event`);
    const r = await postEvent(buildEnvelope("Bash", {}));
    assert.equal(r.decision, "deny");
    assert.match(r.reason, /blocked by test/);
    console.log("OK deny path");
  } finally { await bus.close(); }
}

async function testBusDownFailClosed() {
  // unreachable port
  const { postEvent, buildEnvelope } = await loadPublisher("http://127.0.0.1:1/event");
  const r = await postEvent(buildEnvelope("Bash", {}));
  assert.equal(r.decision, "deny");
  assert.match(r.reason, /unreachable|timeout/i);
  console.log("OK fail-closed on bus-down");
}

(async () => {
  await testEnvelopeShape();
  await testAllowPath();
  await testDenyPath();
  await testBusDownFailClosed();
  console.log("\nALL OPENCLAW BUNDLE TESTS PASSED");
})().catch((e) => { console.error("FAIL:", e); process.exit(1); });
