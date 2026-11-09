// node --test tests/js/  (built-in test runner, no dependency)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const B = require("../../devialet_web/static/spectrum-ballistics.js");

const FPS_MS = 1000 / 60;

function run(state, ms, startMs, hold = true) {
  let t = startMs;
  for (let elapsed = 0; elapsed < ms; elapsed += FPS_MS) {
    t += FPS_MS;
    B.step(state, FPS_MS, t, hold);
  }
  return t;
}

test("dB mapping: 0 dBFS = top, -60 dB = bottom, clamped, NaN = 0", () => {
  assert.equal(B.dbToLevel(0), 1);
  assert.equal(B.dbToLevel(-60), 0);
  assert.equal(B.dbToLevel(-30), 0.5);
  assert.equal(B.dbToLevel(-90), 0);
  assert.equal(B.dbToLevel(6), 1);
  assert.equal(B.dbToLevel(NaN), 0);
  assert.equal(B.dbToLevel(undefined), 0);
});

test("fast rise: the bar reaches the measured level on the next frame", () => {
  const s = B.create(1);
  B.setTargets(s, [-6]);
  B.step(s, FPS_MS, 0, true);
  assert.equal(s.level[0], 0.9);
});

test("slow fall: about 33 dB/s, never below the target", () => {
  const s = B.create(1);
  B.setTargets(s, [0]);
  B.step(s, FPS_MS, 0, true);
  B.setTargets(s, [-60]);
  run(s, 500, 0);
  assert.ok(Math.abs(s.level[0] - (1 - 0.55 * 0.5)) < 0.02, `level ${s.level[0]}`);
  run(s, 3000, 500);
  assert.equal(s.level[0], 0);
});

test("peak hold: the peak stays 800 ms above a falling bar", () => {
  const s = B.create(1);
  B.setTargets(s, [-6]);
  B.step(s, FPS_MS, 0, true);
  B.setTargets(s, [-60]);
  run(s, 780, 0);
  assert.equal(s.peak[0], 0.9, "still held at 780 ms");
  assert.ok(s.level[0] < s.peak[0], "bar has fallen below the peak");
});

test("peak decay: after the hold, the peak falls with acceleration", () => {
  const s = B.create(1);
  B.setTargets(s, [0]);
  B.step(s, FPS_MS, 0, true);
  B.setTargets(s, [-60]);
  let t = run(s, 800, 0);
  const p0 = s.peak[0];
  t = run(s, 100, t);
  const drop1 = p0 - s.peak[0];
  const p1 = s.peak[0];
  t = run(s, 100, t);
  const drop2 = p1 - s.peak[0];
  assert.ok(drop1 > 0 && drop2 > drop1, `accelerating fall ${drop1} then ${drop2}`);
  run(s, 3000, t);
  assert.equal(s.peak[0], s.level[0], "peak ends on the bar");
});

test("a new higher level re-arms the hold", () => {
  const s = B.create(1);
  B.setTargets(s, [-30]);
  B.step(s, FPS_MS, 0, true);
  B.setTargets(s, [-60]);
  let t = run(s, 1200, 0);
  assert.ok(s.peak[0] < 0.5);
  B.setTargets(s, [-12]);
  B.step(s, FPS_MS, t + FPS_MS, true);
  assert.equal(s.peak[0], 0.8);
  B.setTargets(s, [-60]);
  run(s, 700, t + FPS_MS);
  assert.equal(s.peak[0], 0.8);
});

test("peak hold disabled: the peak follows the bar", () => {
  const s = B.create(1);
  B.setTargets(s, [0]);
  B.step(s, FPS_MS, 0, false);
  B.setTargets(s, [-60]);
  run(s, 300, 0, false);
  assert.equal(s.peak[0], s.level[0]);
});

test("silence: every band falls to zero; bands are independent", () => {
  const s = B.create(10);
  B.setTargets(s, [0, -10, -20, -30, -40, -50, -60, -70, -80, -90]);
  B.step(s, FPS_MS, 0, true);
  assert.deepEqual(Array.from(s.level).map((v) => +v.toFixed(3)),
                   [1, 0.833, 0.667, 0.5, 0.333, 0.167, 0, 0, 0, 0]);
  B.setTargets(s, new Array(10).fill(-90));
  run(s, 5000, 0);
  assert.ok(s.level.every((v) => v === 0) && s.peak.every((v) => v === 0));
});

test("a long pause (hidden tab) is clamped to maxStepMs", () => {
  const s = B.create(1);
  B.setTargets(s, [0]);
  B.step(s, FPS_MS, 0, true);
  B.setTargets(s, [-60]);
  B.step(s, 10000, 10000, true);
  assert.ok(Math.abs(s.level[0] - (1 - 0.55 * 0.1)) < 1e-9);
});

test("reset clears levels and peaks", () => {
  const s = B.create(2);
  B.setTargets(s, [0, 0]);
  B.step(s, FPS_MS, 0, true);
  B.reset(s);
  assert.ok(s.level.every((v) => v === 0) && s.peak.every((v) => v === 0));
});

// --- V2.3.1: configurable peak hold duration ----------------------------------

for (const holdMs of [500, 1000, 2000]) {
  test(`peak hold of ${holdMs} ms: held until then, then decays`, () => {
    const s = B.create(1, { peakHoldMs: holdMs });
    B.setTargets(s, [-6]);
    B.step(s, FPS_MS, 0, true);
    B.setTargets(s, [-60]);
    let t = run(s, holdMs - 40, 0);
    assert.equal(s.peak[0], 0.9, `still held at ${holdMs - 40} ms`);
    t = run(s, 200, t);
    assert.ok(s.peak[0] < 0.9, "decaying after the hold");
  });
}

test("changing the hold duration applies immediately to a held peak", () => {
  const s = B.create(1, { peakHoldMs: 2000 });
  B.setTargets(s, [0]);
  B.step(s, FPS_MS, 0, true);
  B.setTargets(s, [-60]);
  let t = run(s, 700, 0);
  assert.equal(s.peak[0], 1, "held under the 2 s setting");
  B.configure(s, { peakHoldMs: 500 });  // user picks 500 ms while the peak is held
  t = run(s, 100, t);
  assert.ok(s.peak[0] < 1, "500 ms already elapsed: the peak starts falling now");
  assert.equal(s.opts.peakHoldMs, 500);
});

test("configure does not alter the other ballistics", () => {
  const s = B.create(1);
  B.configure(s, { peakHoldMs: 1000 });
  assert.equal(s.opts.releasePerSec, B.DEFAULTS.releasePerSec);
  assert.equal(s.opts.peakGravity, B.DEFAULTS.peakGravity);
});
