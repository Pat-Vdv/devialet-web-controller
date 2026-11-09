// node --test tests/js/theme.test.cjs  (built-in runner, no dependency)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const G = require("../../devialet_web/static/theme.js");
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

// Minimal document: <html> dataset/classList, events, option elements.
function fakeDoc() {
  const listeners = {};
  const classes = new Set();
  return {
    documentElement: {
      dataset: {},
      classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) },
    },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    dispatchEvent(ev) { for (const fn of listeners[ev.type] || []) fn(ev); return true; },
    createElement: () => ({ value: "", textContent: "" }),
  };
}
function fakeSelect() {
  const handlers = {};
  return {
    options: [], value: "",
    replaceChildren() { this.options = []; },
    append(o) { this.options.push(o); },
    addEventListener(type, fn) { handlers[type] = fn; },
    choose(value) { this.value = value; handlers.change(); },
  };
}

// --- themes ---------------------------------------------------------------------

test("four global themes: Classic, Technics, Pioneer, Vintage Amber", () => {
  assert.deepEqual(Object.keys(G.THEMES), ["classic", "technics", "pioneer", "amber"]);
  assert.equal(G.THEMES.amber, "Vintage Amber");
  assert.equal(G.DEFAULT_THEME, "technics");
});

test("unknown values fall back to the default theme", () => {
  for (const t of Object.keys(G.THEMES)) assert.equal(G.sanitizeTheme(t), t);
  for (const bad of ["neon", "__proto__", "", null, 3]) assert.equal(G.sanitizeTheme(bad), "technics");
});

test("analyzer palette derives from the global theme (Classic keeps Technics)", () => {
  assert.equal(G.spectrumPalette("classic"), "technics");
  for (const t of ["technics", "pioneer", "amber"]) {
    assert.equal(G.spectrumPalette(t), t);
    assert.ok(t in S.THEMES, `${t} exists as an analyzer palette`);
  }
});

test("applying a theme sets <html data-theme> and notifies listeners", () => {
  const doc = fakeDoc();
  const seen = [];
  doc.addEventListener(G.EVENT, (e) => seen.push(e.detail));
  for (const t of Object.keys(G.THEMES)) G.applyTheme(doc, t);
  assert.equal(doc.documentElement.dataset.theme, "amber");
  assert.deepEqual(seen.map((d) => d.theme), ["classic", "technics", "pioneer", "amber"]);
  assert.deepEqual(seen.map((d) => d.palette), ["technics", "technics", "pioneer", "amber"]);
  assert.equal(G.currentTheme(doc), "amber");
});

// --- persistence ----------------------------------------------------------------

test("save then load restores the theme", () => {
  const storage = new MemoryStorage();
  for (const t of Object.keys(G.THEMES)) {
    assert.equal(G.saveTheme(storage, t), true);
    assert.deepEqual(G.loadTheme(storage), { theme: t, migrated: false });
  }
});

test("nothing stored gives the default; blocked or corrupt storage never throws", () => {
  assert.deepEqual(G.loadTheme(new MemoryStorage()), { theme: "technics", migrated: false });
  assert.deepEqual(G.loadTheme(throwingStorage), { theme: "technics", migrated: false });
  assert.deepEqual(G.loadTheme(null), { theme: "technics", migrated: false });
  assert.equal(G.saveTheme(throwingStorage, "amber"), false);
  const corrupt = new MemoryStorage({ [G.LEGACY_KEY]: "{not json" });
  assert.deepEqual(G.loadTheme(corrupt), { theme: "technics", migrated: false });
  const invalid = new MemoryStorage({ [G.STORAGE_KEY]: "neon" });
  assert.equal(G.loadTheme(invalid).theme, "technics");
});

// --- migration from V2.3.1 --------------------------------------------------------

test("V2.3.1 analyzer theme becomes the global theme, other prefs untouched", () => {
  const v231 = { enabled: true, theme: "pioneer", sensitivity: 6, peakHold: false, peakHoldMs: 2000, mode: "mono" };
  const storage = new MemoryStorage({ [G.LEGACY_KEY]: JSON.stringify(v231) });
  assert.deepEqual(G.loadTheme(storage), { theme: "pioneer", migrated: true });
  const doc = fakeDoc();
  assert.equal(G.boot(doc, storage), "pioneer");
  assert.equal(doc.documentElement.dataset.theme, "pioneer");
  assert.equal(storage.getItem(G.STORAGE_KEY), "pioneer", "migration persisted");
  // sensitivity, peak hold, hold duration and mode are still there, unchanged
  const prefs = S.load(storage);
  assert.equal(prefs.sensitivity, 6);
  assert.equal(prefs.peakHold, false);
  assert.equal(prefs.peakHoldMs, 2000);
  assert.equal(prefs.mode, "mono");
  assert.equal(prefs.enabled, true);
});

test("a saved global theme wins over the legacy one", () => {
  const storage = new MemoryStorage({
    [G.STORAGE_KEY]: "classic",
    [G.LEGACY_KEY]: JSON.stringify({ theme: "amber" }),
  });
  assert.deepEqual(G.loadTheme(storage), { theme: "classic", migrated: false });
});

test("fullscreen is never part of the stored theme", () => {
  const storage = new MemoryStorage();
  G.saveTheme(storage, "amber");
  assert.deepEqual(Object.keys(storage.data), [G.STORAGE_KEY]);
  assert.equal(storage.getItem(G.STORAGE_KEY), "amber");
});

// --- boot: no flash, power-on once ---------------------------------------------------

test("boot applies the theme synchronously and starts the power-on animation once", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const doc = fakeDoc();
  const storage = new MemoryStorage({ [G.STORAGE_KEY]: "amber" });
  G.boot(doc, storage);
  assert.equal(doc.documentElement.dataset.theme, "amber", "set before any paint");
  assert.equal(doc.documentElement.classList.contains("power-on"), true);
  t.mock.timers.tick(G.POWER_ON_MS);
  assert.equal(doc.documentElement.classList.contains("power-on"), false);
  G.applyTheme(doc, "pioneer");                        // later theme change
  assert.equal(doc.documentElement.classList.contains("power-on"), false, "not replayed");
});

// --- single selector ------------------------------------------------------------------

test("the selector lists the four themes, saves and applies a change at once", () => {
  const doc = fakeDoc();
  const storage = new MemoryStorage();
  G.boot(doc, storage);
  const select = fakeSelect();
  G.bindSelect(select, doc, storage);
  assert.deepEqual(select.options.map((o) => o.value), ["classic", "technics", "pioneer", "amber"]);
  assert.equal(select.value, "technics");
  const palettes = [];
  doc.addEventListener(G.EVENT, (e) => palettes.push(e.detail.palette));
  select.choose("classic");
  assert.equal(doc.documentElement.dataset.theme, "classic");
  assert.equal(storage.getItem(G.STORAGE_KEY), "classic");
  assert.deepEqual(palettes, ["technics"]);
  G.applyTheme(doc, "amber");                          // changed elsewhere: select follows
  assert.equal(select.value, "amber");
});

test("fullscreen: the one theme control moves into the analyzer and back", () => {
  const node = (name) => ({ name, children: [], appendChild(c) {
    if (c.parentNode) c.parentNode.children.splice(c.parentNode.children.indexOf(c), 1);
    this.children.push(c); c.parentNode = this;
  } });
  const home = node("header"), analyzer = node("spectrum-controls");
  const control = { name: "theme-control", parentNode: null };
  home.appendChild(control);
  assert.equal(G.dock(control, home, analyzer, true), analyzer);
  assert.equal(control.parentNode, analyzer);
  assert.equal(home.children.length + analyzer.children.length, 1, "never duplicated");
  G.dock(control, home, analyzer, true);              // idempotent
  assert.equal(analyzer.children.length, 1);
  G.dock(control, home, analyzer, false);             // Esc / Quitter
  assert.equal(control.parentNode, home);
  assert.equal(analyzer.children.length, 0);
});

test("theme change while in fullscreen reaches the analyzer palette", () => {
  const doc = fakeDoc();
  const card = { dataset: { theme: "technics" } };
  doc.addEventListener(G.EVENT, (e) => { card.dataset.theme = e.detail.palette; });  // as spectrum.js
  G.applyTheme(doc, "amber");
  assert.equal(card.dataset.theme, "amber");
  G.applyTheme(doc, "classic");
  assert.equal(card.dataset.theme, "technics");
});
