// node --test tests/js/eq-core.test.cjs  (built-in runner, no dependency)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const E = require("../../devialet_web/static/eq-core.js");
const T = require("../../devialet_web/static/tone-core.js");

const flush = () => new Promise((r) => setImmediate(r));

test("ten bands, ±12 dB, 0.5 dB step, same as the backend", () => {
  assert.deepEqual([...E.BANDS], [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]);
  assert.deepEqual([E.MIN, E.MAX, E.STEP], [-12, 12, 0.5]);
  assert.deepEqual([...E.KEYS], E.BANDS.map(String));
});

test("normalizeGain snaps to 0.5 dB and clamps", () => {
  assert.equal(E.normalizeGain("-3.5"), -3.5);
  assert.equal(E.normalizeGain(2.3), 2.5);
  assert.equal(E.normalizeGain(2.2), 2);
  assert.equal(E.normalizeGain(40), 12);
  assert.equal(E.normalizeGain(-40), -12);
  for (const bad of [null, true, "", "x", NaN, Infinity]) assert.equal(E.normalizeGain(bad), null, String(bad));
});

test("readouts: short gain, signed, French decimal comma", () => {
  assert.equal(E.formatGain(0), "0");
  assert.equal(E.formatGain(6), "+6");
  assert.equal(E.formatGain(-1.5), "−1,5");
  assert.equal(E.formatGain(null), "—");
  assert.equal(E.formatDb(-12), "−12 dB");
  assert.equal(E.formatDb(undefined), "— dB");
});

test("band labels", () => {
  assert.deepEqual(E.BANDS.map(E.freqLabel), ["31", "62", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"]);
  assert.equal(E.bandName(1000), "1 kHz");
  assert.equal(E.bandName(62), "62 Hz");
});

test("scale marks: 0 dB is exactly in the middle of the travel", () => {
  assert.equal(E.gainToFraction(0), 0.5);
  assert.equal(E.gainToFraction(12), 1);
  assert.equal(E.gainToFraction(-12), 0);
  assert.equal(E.gainToFraction(6), 0.75);
});

const ok = (over = {}) => ({ status: "ok", enabled: true, gains: Array(10).fill(0), preamp: 0, ...over });

test("describe: synchronised, nothing to reset when flat", () => {
  const v = E.describe(ok());
  assert.deepEqual([v.pill, v.label, v.editable, v.bypassed, v.canReset, v.alert], ["pill-ok", "Synchronisé", true, false, false, false]);
  assert.equal(E.describe(ok({ gains: [0, 0, 0, 0, 0, -12, 0, 0, 0, 0] })).canReset, true);
});

test("describe: bypassed keeps the faders editable, plain information", () => {
  const v = E.describe(ok({ enabled: false, gains: [3, 0, 0, 0, 0, 0, 0, 0, 0, 0] }));
  assert.equal(v.label, "Désactivé");
  assert.equal(v.editable, true);
  assert.equal(v.bypassed, true);
  assert.equal(v.alert, false);
  assert.match(v.lines.join(), /réglages conservés/);
});

test("describe: sending and errors", () => {
  assert.equal(E.describe(ok(), { busy: true }).label, "Envoi…");
  const v = E.describe(ok(), { error: "Échec : x" });
  assert.deepEqual([v.pill, v.alert], ["pill-bad", true]);
});

test("describe: file problems lock the faders; only a missing file can be reset", () => {
  for (const [status, label, canReset] of [["missing", "Fichier absent", true], ["unmanaged", "Non géré", false],
    ["inconsistent", "Modifié à la main", false], ["disabled", "Non configuré", false],
    ["unavailable", "Introuvable", false], ["server", "Hors ligne", false]]) {
    const v = E.describe({ status, message: "m" });
    assert.equal(v.label, label, status);
    assert.equal(v.editable, false, status);
    assert.equal(v.canReset, canReset, status);
    assert.equal(v.alert, true, status);
  }
  assert.equal(E.describe(null).editable, false);
});

test("tone coalescer reused for the ten bands: merged, latest wins, 0.5 steps", async () => {
  const sent = [];
  const sync = T.createToneSync({
    send: (body) => { sent.push(body); return Promise.resolve({}); },
    bands: E.KEYS, normalize: E.normalizeGain, intervalMs: 0,
  });
  sync.submit("1000", -3);
  sync.submit("1000", "-6.3");
  sync.submit("4000", 2.4);
  await sync.idle();
  await flush();
  assert.deepEqual(sent, [{ 1000: -6.5, 4000: 2.5 }]);
  assert.throws(() => sync.submit("1001", 1), /bande inconnue/);
  assert.throws(() => sync.submit("bass", 1), /bande inconnue/);
});

test("tone coalescer defaults unchanged (bass/treble, whole dB)", async () => {
  const sent = [];
  const sync = T.createToneSync({ send: (b) => { sent.push(b); return Promise.resolve({}); }, intervalMs: 0 });
  sync.submit("bass", 2.4);
  await sync.idle();
  assert.deepEqual(sent, [{ bass: 2 }]);
  assert.throws(() => sync.submit("1000", 1), /bande inconnue/);
});

// --- presets (V2.4.3-B4) -----------------------------------------------------
const PRESETS = [
  { id: "flat", label: "Flat", gains: Array(10).fill(0), preamp: 0 },
  { id: "rock", label: "Rock", gains: [3, 2.5, 1, 0, -1, -1, 0, 1, 2, 2.5], preamp: -4.5 },
];

test("presetButtons: server list then Custom, the current one pressed", () => {
  const b = E.presetButtons(ok({ presets: PRESETS, preset: "rock", custom: null }));
  assert.deepEqual(b.map((x) => x.id), ["flat", "rock", "custom"]);
  assert.deepEqual(b.map((x) => x.pressed), [false, true, false]);
  assert.equal(b[1].title, "Rock · préampli −4,5 dB");
  assert.equal(b[2].disabled, true);                         // nothing to recall yet
  assert.match(b[2].title, /Aucun réglage manuel/);
});

test("presetButtons: Custom recalls a recorded curve", () => {
  const b = E.presetButtons(ok({ presets: PRESETS, preset: "custom", custom: [1, 0, 0, 0, 0, 0, 0, 0, 0, 0] }));
  assert.deepEqual(b.at(-1), { id: "custom", label: "Custom", pressed: true, disabled: false,
                               title: "Rappeler le dernier réglage manuel" });
});

test("presetButtons: locked while sending or when the file is not usable", () => {
  assert.ok(E.presetButtons(ok({ presets: PRESETS, custom: [] }), { busy: true }).every((x) => x.disabled));
  const bad = E.presetButtons({ status: "unmanaged", presets: PRESETS, preset: "rock", custom: [] });
  assert.ok(bad.every((x) => x.disabled && !x.pressed));
  assert.deepEqual(E.presetButtons(null).map((x) => x.id), ["custom"]);   // before the first answer
});

test("confirmText: rise and limit with a French decimal comma", () => {
  assert.equal(E.confirmText({ jumpDb: 13, limitDb: 6 }),
    "Le niveau va monter jusqu'à +13 dB sur certaines fréquences (plus de 6 dB). " +
    "Baissez le volume si nécessaire avant d'appliquer.");
  assert.match(E.confirmText({ jumpDb: 6.6, limitDb: 6 }), /\+6,6 dB/);
});
