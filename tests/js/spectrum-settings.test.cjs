// node --test tests/js/spectrum-settings.test.cjs  (built-in runner, no dependency)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const S = require("../../devialet_web/static/spectrum-settings.js");

class MemoryStorage {
  constructor(initial) { this.data = Object.assign({}, initial); }
  getItem(k) { return Object.prototype.hasOwnProperty.call(this.data, k) ? this.data[k] : null; }
  setItem(k, v) { this.data[k] = String(v); }
}

const throwingStorage = {
  getItem() { throw new Error("SecurityError"); },
  setItem() { throw new Error("QuotaExceededError"); },
};

// --- themes -----------------------------------------------------------------------

test("three themes are declared: Technics, Pioneer, Vintage Amber", () => {
  assert.deepEqual(Object.keys(S.THEMES), ["technics", "pioneer", "amber"]);
  assert.equal(S.THEMES.amber, "Vintage Amber");
});

test("theme selection: valid themes kept, unknown theme falls back to Technics", () => {
  for (const theme of Object.keys(S.THEMES)) assert.equal(S.sanitize({ theme }).theme, theme);
  assert.equal(S.sanitize({ theme: "neon" }).theme, "technics");
  assert.equal(S.sanitize({ theme: "__proto__" }).theme, "technics");
  assert.equal(S.sanitize({ theme: 3 }).theme, "technics");
});

test("palette is read from the CSS variables of the active theme", () => {
  const vars = {
    "--spec-seg-low": " #ff6a00", "--spec-seg-high": "#ffbf2e", "--spec-seg-off": "rgba(255, 140, 20, 0.08)",
    "--spec-peak": "#fff4a6", "--spec-peak-glow": "rgba(255,220,120,.9)", "--spec-grid": "rgba(1,2,3,.1)",
    "--spec-label": "#8a6a3e", "--spec-accent": "#ffaa2b",
  };
  const p = S.readPalette((name) => vars[name]);
  assert.deepEqual(p.low, [255, 106, 0]);
  assert.deepEqual(p.high, [255, 191, 46]);
  assert.equal(p.peak, "#fff4a6");
  assert.equal(p.accent, "#ffaa2b");
});

test("palette falls back to Technics colours when variables are missing", () => {
  const p = S.readPalette(() => "");
  assert.deepEqual(p.low, [25, 225, 255]);
  assert.equal(p.peak, "#ffffff");
});

test("colour helpers", () => {
  assert.deepEqual(S.parseColor("#1ef"), [17, 238, 255]);
  assert.deepEqual(S.parseColor("rgb(1, 2, 3)"), [1, 2, 3]);
  assert.deepEqual(S.parseColor("nonsense", [9, 9, 9]), [9, 9, 9]);
  assert.equal(S.mixColor([0, 0, 0], [255, 255, 255], 0.5), "rgb(128,128,128)");
  assert.equal(S.mixColor([0, 0, 0], [255, 255, 255], 2), "rgb(255,255,255)");
});

// --- preferences ---------------------------------------------------------------------

test("defaults when nothing is stored", () => {
  assert.deepEqual(S.load(new MemoryStorage()), S.DEFAULTS);
  assert.equal(S.DEFAULTS.peakHoldMs, 1000);
});

test("save then load restores theme, sensitivity, hold, mode and peak hold", () => {
  const storage = new MemoryStorage();
  const prefs = { enabled: true, theme: "amber", sensitivity: -6, peakHold: false, peakHoldMs: 2000, mode: "mono" };
  assert.equal(S.save(storage, prefs), true);
  assert.deepEqual(S.load(storage), prefs);
});

test("fullscreen and unknown keys are never persisted nor restored", () => {
  const storage = new MemoryStorage();
  S.save(storage, Object.assign({}, S.DEFAULTS, { fullscreen: true, junk: 1 }));
  const raw = JSON.parse(storage.getItem(S.STORAGE_KEY));
  assert.equal("fullscreen" in raw, false);
  const restored = S.load(new MemoryStorage({ [S.STORAGE_KEY]: JSON.stringify({ fullscreen: true }) }));
  assert.equal("fullscreen" in restored, false);
});

test("corrupt JSON or blocked storage give defaults, without throwing", () => {
  assert.deepEqual(S.load(new MemoryStorage({ [S.STORAGE_KEY]: "{not json" })), S.DEFAULTS);
  assert.deepEqual(S.load(throwingStorage), S.DEFAULTS);
  assert.deepEqual(S.load(null), S.DEFAULTS);
  assert.equal(S.save(throwingStorage, S.DEFAULTS), false);
});

test("V2.3 preferences (no theme/sensitivity) are upgraded with defaults", () => {
  const storage = new MemoryStorage({ [S.STORAGE_KEY]: JSON.stringify({ enabled: true, peakHold: false, mode: "mono" }) });
  const p = S.load(storage);
  assert.equal(p.enabled, true);
  assert.equal(p.peakHold, false);
  assert.equal(p.mode, "mono");
  assert.equal(p.theme, "technics");
  assert.equal(p.sensitivity, 0);
  assert.equal(p.peakHoldMs, 1000);
});

// --- sensitivity ----------------------------------------------------------------------

test("sensitivity is clamped to -12..+12 dB and rounded", () => {
  assert.equal(S.sanitize({ sensitivity: 30 }).sensitivity, 12);
  assert.equal(S.sanitize({ sensitivity: -30 }).sensitivity, -12);
  assert.equal(S.sanitize({ sensitivity: 3.6 }).sensitivity, 4);
  assert.equal(S.sanitize({ sensitivity: "-5" }).sensitivity, -5);
  assert.equal(S.sanitize({ sensitivity: "abc" }).sensitivity, 0);
  assert.equal(S.sanitize({ sensitivity: null }).sensitivity, 0);
});

test("sensitivity shifts the displayed levels only and never mutates the data", () => {
  const frame = [-20, -40, -90];
  const shown = S.applySensitivity(frame, 6);
  assert.deepEqual(shown, [-14, -34, -84]);
  assert.deepEqual(frame, [-20, -40, -90], "input untouched");
  assert.deepEqual(S.applySensitivity(frame, -12), [-32, -52, -102]);
  assert.deepEqual(S.applySensitivity(frame, NaN), frame);
});

test("sensitivity label", () => {
  assert.equal(S.formatSensitivity(0), "0 dB");
  assert.equal(S.formatSensitivity(6), "+6 dB");
  assert.equal(S.formatSensitivity(-12), "−12 dB");
});

// --- peak hold durations ------------------------------------------------------------

test("peak hold durations: only 500, 1000 and 2000 ms are accepted", () => {
  assert.deepEqual(Array.from(S.PEAK_HOLD_OPTIONS), [500, 1000, 2000]);
  for (const ms of [500, 1000, 2000, "2000"]) assert.equal(S.sanitize({ peakHoldMs: ms }).peakHoldMs, Number(ms));
  for (const ms of [0, 800, 5000, "x", null]) assert.equal(S.sanitize({ peakHoldMs: ms }).peakHoldMs, 1000);
});

test("mode and peak hold switch validation", () => {
  assert.equal(S.sanitize({ mode: "mono" }).mode, "mono");
  assert.equal(S.sanitize({ mode: "surround" }).mode, "stereo");
  assert.equal(S.sanitize({ peakHold: false }).peakHold, false);
  assert.equal(S.sanitize({ peakHold: "no" }).peakHold, true);
});

// --- fullscreen ---------------------------------------------------------------------

function fakeDom({ reject = null, prefixed = false, enabled = true, exitReject = null } = {}) {
  const doc = { fullscreenElement: null, fullscreenEnabled: enabled };
  const element = {};
  const request = async function () {
    if (reject) throw new Error(reject);
    doc[prefixed ? "webkitFullscreenElement" : "fullscreenElement"] = this;
  };
  const exit = async function () {
    if (exitReject) throw new Error(exitReject);
    doc.fullscreenElement = null;
    doc.webkitFullscreenElement = null;
  };
  if (prefixed) {
    element.webkitRequestFullscreen = request;
    doc.webkitExitFullscreen = exit;
    delete doc.fullscreenEnabled;
    doc.webkitFullscreenEnabled = enabled;
  } else {
    element.requestFullscreen = request;
    doc.exitFullscreen = exit;
  }
  return { doc, element };
}

test("fullscreen: enter, active, exit (standard API)", async () => {
  const { doc, element } = fakeDom();
  const fs = S.createFullscreen(element, doc);
  assert.equal(fs.supported(), true);
  assert.deepEqual(await fs.enter(), { ok: true });
  assert.equal(fs.active(), true);
  assert.deepEqual(await fs.exit(), { ok: true });
  assert.equal(fs.active(), false);
});

test("fullscreen: toggle and Escape-like external exit", async () => {
  const { doc, element } = fakeDom();
  const fs = S.createFullscreen(element, doc);
  await fs.toggle();
  assert.equal(fs.active(), true);
  doc.fullscreenElement = null;          // the browser left fullscreen (Esc)
  assert.equal(fs.active(), false);
  assert.deepEqual(await fs.exit(), { ok: true }, "exit when already out is a no-op");
  await fs.toggle();
  assert.equal(fs.active(), true);
});

test("fullscreen: webkit-prefixed API is used when the standard one is missing", async () => {
  const { doc, element } = fakeDom({ prefixed: true });
  const fs = S.createFullscreen(element, doc);
  assert.equal(fs.supported(), true);
  assert.deepEqual(await fs.enter(), { ok: true });
  assert.equal(fs.active(), true);
  await fs.exit();
  assert.equal(fs.active(), false);
});

test("fullscreen errors are reported, never thrown", async () => {
  const refused = fakeDom({ reject: "Permissions check failed" });
  const r = await S.createFullscreen(refused.element, refused.doc).enter();
  assert.equal(r.ok, false);
  assert.match(r.error, /Plein écran refusé : Permissions check failed/);

  const disabled = fakeDom({ enabled: false });
  const d = await S.createFullscreen(disabled.element, disabled.doc).enter();
  assert.equal(d.ok, false);
  assert.match(d.error, /non pris en charge/);

  const none = await S.createFullscreen({}, {}).enter();
  assert.equal(none.ok, false);

  const stuck = fakeDom({ exitReject: "busy" });
  const fs = S.createFullscreen(stuck.element, stuck.doc);
  await fs.enter();
  const e = await fs.exit();
  assert.equal(e.ok, false);
  assert.match(e.error, /busy/);
});
