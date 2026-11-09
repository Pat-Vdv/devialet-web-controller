// node --test tests/js/tone-core.test.cjs  (built-in runner, no dependency)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const T = require("../../devialet_web/static/tone-core.js");

// Manual clock + timers so the coalescer is tested deterministically.
function fakeClock() {
  let t = 0;
  const timers = [];
  return {
    now: () => t,
    setTimer(fn, ms) { const h = { at: t + ms, fn }; timers.push(h); return h; },
    clearTimer(h) { const i = timers.indexOf(h); if (i >= 0) timers.splice(i, 1); },
    async advance(ms) {
      t += ms;
      for (;;) {
        timers.sort((a, b) => a.at - b.at);
        const next = timers[0];
        if (!next || next.at > t) break;
        timers.shift();
        next.fn();
        await new Promise((r) => setImmediate(r));
      }
      await new Promise((r) => setImmediate(r));
    },
    get timers() { return timers.length; },
  };
}

function fakeServer({ fail = null, delayMs = 0 } = {}) {
  const sent = [];
  const values = { bass: 0, treble: 0 };
  return {
    sent, values,
    send(body) {
      sent.push(body);
      return new Promise((resolve, reject) => setTimeout(() => {
        if (fail) return reject(new Error(fail));
        Object.assign(values, body);
        resolve({ available: true, ...values, confirmed: true });
      }, delayMs));
    },
  };
}

// --- values -------------------------------------------------------------------------

test("range is -6..+6 dB with 0 as neutral", () => {
  assert.equal(T.MIN, -6);
  assert.equal(T.MAX, 6);
  assert.equal(T.clampGain(9), 6);
  assert.equal(T.clampGain(-9), -6);
  assert.equal(T.clampGain(2.6), 3);
  assert.equal(T.clampGain("-2"), -2);
  for (const bad of [null, undefined, "x", "", true, NaN]) assert.equal(T.clampGain(bad), null);
});

test("dB formatting", () => {
  assert.equal(T.formatDb(0), "0 dB");
  assert.equal(T.formatDb(3), "+3 dB");
  assert.equal(T.formatDb(-6), "−6 dB");
  assert.equal(T.formatDb(1.5), "+1,5 dB");
  assert.equal(T.formatDb(null), "— dB");
});

test("knob geometry: -6 at -135°, 0 at top, +6 at +135°", () => {
  assert.equal(T.valueToAngle(-6), -135);
  assert.equal(T.valueToAngle(0), 0);
  assert.equal(T.valueToAngle(6), 135);
  assert.equal(T.valueToAngle(3), 67.5);
  assert.equal(T.valueToAngle(99), 135);
});

// --- keyboard ------------------------------------------------------------------------

test("keyboard follows the ARIA slider pattern", () => {
  assert.equal(T.keyToValue("ArrowUp", 0), 1);
  assert.equal(T.keyToValue("ArrowRight", 0), 1);
  assert.equal(T.keyToValue("ArrowDown", 0), -1);
  assert.equal(T.keyToValue("ArrowLeft", 0), -1);
  assert.equal(T.keyToValue("PageUp", 2), 5);
  assert.equal(T.keyToValue("PageDown", -5), -6);
  assert.equal(T.keyToValue("Home", 3), -6);
  assert.equal(T.keyToValue("End", -3), 6);
  for (const k of ["0", "Delete", "Backspace"]) assert.equal(T.keyToValue(k, 4), 0);
  assert.equal(T.keyToValue("ArrowUp", 6), 6, "bounded");
  assert.equal(T.keyToValue("Tab", 2), null, "unhandled keys keep their default");
  assert.equal(T.keyToValue("a", 2), null);
});

test("knob keydown: changes value, reports handled keys, no change event at a bound", () => {
  const changes = [];
  const k = T.createKnob({ value: 5, onChange: (v) => changes.push(v) });
  assert.equal(k.keydown("ArrowUp"), true);
  assert.equal(k.keydown("ArrowUp"), true, "still handled at the bound (prevents page scroll)");
  assert.equal(k.keydown("Tab"), false);
  assert.equal(k.keydown("0"), true);
  assert.deepEqual(changes, [6, 0]);
});

// --- mouse -------------------------------------------------------------------------

test("drag: up or right raises, down or left lowers, 14 px per dB, clamped", () => {
  assert.equal(T.dragToValue(0, 0, -28), 2);
  assert.equal(T.dragToValue(0, 28, 0), 2);
  assert.equal(T.dragToValue(0, 0, 42), -3);
  assert.equal(T.dragToValue(0, 0, 13), 0, "below one step: no change");
  assert.equal(T.dragToValue(4, 0, -500), 6);
  assert.equal(T.dragToValue(-4, -500, 0), -6);
});

test("knob drag emits one change per dB step, not per pixel", () => {
  const changes = [];
  const k = T.createKnob({ onChange: (v) => changes.push(v) });
  k.pointerDown(100, 100);
  assert.equal(k.dragging, true);
  for (let y = 99; y >= 30; y--) k.pointerMove(100, y);   // 70 px upwards
  k.pointerUp();
  assert.equal(k.dragging, false);
  assert.deepEqual(changes, [1, 2, 3, 4, 5]);
  k.pointerMove(100, 0);
  assert.equal(k.value, 5, "moves after release are ignored");
});

test("double-click resets to 0 dB", () => {
  const changes = [];
  const k = T.createKnob({ value: -4, onChange: (v) => changes.push(v) });
  assert.equal(k.reset(), true);
  assert.equal(k.value, 0);
  assert.equal(k.reset(), false, "already neutral: nothing sent");
  assert.deepEqual(changes, [0]);
});

test("wheel: one dB per notch", () => {
  const k = T.createKnob({ value: 0 });
  k.wheel(-100);
  k.wheel(-3);
  assert.equal(k.value, 2);
  k.wheel(120);
  assert.equal(k.value, 1);
  assert.equal(k.wheel(0), false);
});

test("values read from the speakers never override a drag in progress", () => {
  const k = T.createKnob({ value: 0 });
  k.sync(3);
  assert.equal(k.value, 3);
  k.pointerDown(0, 0);
  k.pointerMove(0, -14);
  k.sync(-2);
  assert.equal(k.value, 4);
  k.pointerUp();
  k.sync(-2);
  assert.equal(k.value, -2);
  k.sync(null);
  assert.equal(k.value, -2);
});

test("disabled knob (speakers unavailable) ignores every input", () => {
  const changes = [];
  const k = T.createKnob({ onChange: (v) => changes.push(v) });
  k.setDisabled(true);
  assert.equal(k.keydown("ArrowUp"), false);
  k.pointerDown(0, 0);
  k.pointerMove(0, -100);
  k.wheel(-1);
  k.reset();
  assert.deepEqual(changes, []);
  assert.equal(k.dragging, false);
});

// --- write coalescing ------------------------------------------------------------------

test("a fast drag is coalesced: one request in flight, latest value wins", async () => {
  const clock = fakeClock();
  const server = fakeServer({ delayMs: 5 });
  const states = [];
  const sync = T.createToneSync({ send: server.send, onState: (s) => states.push(s), ...clock });
  for (let v = 1; v <= 6; v++) sync.submit("bass", v);   // six steps within one tick
  assert.equal(sync.busy(), true);
  await clock.advance(0);
  await sync.idle();
  assert.deepEqual(server.sent, [{ bass: 6 }]);
  assert.equal(states.at(-1).bass, 6);
  assert.equal(sync.busy(), false);
});

test("changes made while a request is in flight are merged into the next one", async () => {
  const clock = fakeClock();
  const server = fakeServer({ delayMs: 20 });
  const sync = T.createToneSync({ send: server.send, ...clock });
  sync.submit("bass", 2);
  await clock.advance(0);                     // first request leaves
  sync.submit("bass", 3);
  sync.submit("treble", -1);
  sync.submit("bass", 4);
  assert.deepEqual(sync.pending(), { bass: 4, treble: -1 });
  await sync.idle();
  assert.deepEqual(server.sent, [{ bass: 2 }, { bass: 4, treble: -1 }]);
  assert.deepEqual(server.values, { bass: 4, treble: -1 });
});

test("writes are spaced by the minimum interval", async () => {
  const clock = fakeClock();
  const server = fakeServer();
  const sync = T.createToneSync({ send: server.send, intervalMs: 200, ...clock });
  sync.submit("treble", 1);
  await clock.advance(0);
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(server.sent.length, 1);
  sync.submit("treble", 2);
  await clock.advance(100);
  assert.equal(server.sent.length, 1, "too early");
  await clock.advance(100);
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(server.sent, [{ treble: 1 }, { treble: 2 }]);
});

test("errors are reported and the sync becomes idle again", async () => {
  const clock = fakeClock();
  const server = fakeServer({ fail: "Enceinte injoignable" });
  const errors = [];
  const states = [];
  const sync = T.createToneSync({
    send: server.send, onError: (e, body) => errors.push([e.message, body]),
    onState: (s) => states.push(s), ...clock,
  });
  sync.submit("bass", 5);
  await sync.idle();
  assert.deepEqual(errors, [["Enceinte injoignable", { bass: 5 }]]);
  assert.deepEqual(states, [], "a failed write is never reported as applied");
  assert.equal(sync.busy(), false);
});

test("activity callback exposes pending targets and sending state", async () => {
  const clock = fakeClock();
  const server = fakeServer({ delayMs: 5 });
  const seen = [];
  const sync = T.createToneSync({ send: server.send, onActivity: (a) => seen.push(a), ...clock });
  sync.submit("bass", -3);
  assert.deepEqual(seen[0], { pending: { bass: -3 }, sending: false });
  await clock.advance(0);
  assert.deepEqual(seen.at(-1), { pending: {}, sending: true });
  await sync.idle();
  assert.deepEqual(seen.at(-1), { pending: {}, sending: false });
});

test("invalid submissions are ignored or refused", () => {
  const clock = fakeClock();
  const sync = T.createToneSync({ send: () => Promise.resolve({}), ...clock });
  sync.submit("bass", "x");
  assert.equal(sync.busy(), false);
  sync.submit("bass", 42);
  assert.deepEqual(sync.pending(), { bass: 6 }, "clamped to the range");
  assert.throws(() => sync.submit("balance", 1), /bande inconnue/);
});
