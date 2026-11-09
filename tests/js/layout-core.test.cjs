// node --test tests/js/layout-core.test.cjs  (built-in runner, no dependency)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const L = require("../../devialet_web/static/layout-core.js");

const DEFAULT = ["player", "spectrum", "audio", "eq", "bluetooth"];

class MemoryStorage {
  constructor(initial) { this.data = Object.assign({}, initial); }
  getItem(k) { return Object.prototype.hasOwnProperty.call(this.data, k) ? this.data[k] : null; }
  setItem(k, v) { this.data[k] = String(v); }
  removeItem(k) { delete this.data[k]; }
}
const throwingStorage = {
  getItem() { throw new Error("SecurityError"); },
  setItem() { throw new Error("QuotaExceededError"); },
  removeItem() { throw new Error("SecurityError"); },
};
const stored = (order) => new MemoryStorage({ [L.STORAGE_KEY]: JSON.stringify({ version: 1, order }) });

test("nothing saved: the HTML order", () => {
  assert.deepEqual(L.loadOrder(new MemoryStorage(), DEFAULT), { order: DEFAULT, saved: false, repaired: false });
  assert.deepEqual(L.loadOrder(null, DEFAULT).order, DEFAULT);
});

test("save then load gives the same order (restored after a reload)", () => {
  const s = new MemoryStorage();
  const order = ["eq", "player", "spectrum", "audio", "bluetooth"];
  assert.equal(L.saveOrder(s, order), true);
  assert.deepEqual(JSON.parse(s.getItem(L.STORAGE_KEY)), { version: 1, order });
  assert.deepEqual(L.loadOrder(s, DEFAULT), { order, saved: true, repaired: false });
});

test("move a block to any place, bounded", () => {
  assert.deepEqual(L.move(DEFAULT, "eq", 0), ["eq", "player", "spectrum", "audio", "bluetooth"]);
  assert.deepEqual(L.move(DEFAULT, "player", 4), ["spectrum", "audio", "eq", "bluetooth", "player"]);
  assert.deepEqual(L.move(DEFAULT, "player", 99), ["spectrum", "audio", "eq", "bluetooth", "player"]);
  assert.deepEqual(L.move(DEFAULT, "eq", -5), ["eq", "player", "spectrum", "audio", "bluetooth"]);
  assert.deepEqual(L.move(DEFAULT, "ghost", 0), DEFAULT);
  assert.notEqual(L.move(DEFAULT, "eq", 3), DEFAULT);            // a new array, input untouched
});

test("Monter / Descendre: one place, nothing past the ends", () => {
  assert.deepEqual(L.moveBy(DEFAULT, "eq", -1), ["player", "spectrum", "eq", "audio", "bluetooth"]);
  assert.deepEqual(L.moveBy(DEFAULT, "eq", 1), ["player", "spectrum", "audio", "bluetooth", "eq"]);
  assert.deepEqual(L.moveBy(DEFAULT, "player", -1), DEFAULT);
  assert.deepEqual(L.moveBy(DEFAULT, "bluetooth", 1), DEFAULT);
});

test("invalid saved data gives the default order and asks for a repair", () => {
  for (const raw of ["garbage{", "null", "42", '"eq"', '{"order":"eq"}', "{}", "[1,2]"]) {
    const r = L.loadOrder(new MemoryStorage({ [L.STORAGE_KEY]: raw }), DEFAULT);
    assert.deepEqual(r.order, DEFAULT, raw);
    assert.equal(r.repaired, true, raw);
  }
});

test("unknown ids are dropped, no block is lost, duplicates ignored", () => {
  const r = L.loadOrder(stored(["ghost", "bluetooth", "eq", "eq", 7, "<script>"]), DEFAULT);
  assert.deepEqual(r.order, ["player", "spectrum", "audio", "bluetooth", "eq"]);
  assert.equal(r.repaired, true);
  assert.equal(new Set(r.order).size, DEFAULT.length);
});

test("a bare array (hand-made) is accepted", () => {
  const s = new MemoryStorage({ [L.STORAGE_KEY]: JSON.stringify(["eq", "player", "spectrum", "audio", "bluetooth"]) });
  assert.deepEqual(L.loadOrder(s, DEFAULT).order, ["eq", "player", "spectrum", "audio", "bluetooth"]);
});

test("migration: a block added to the page later lands next to its default neighbour", () => {
  const saved = ["eq", "player", "spectrum", "audio", "bluetooth"];
  // new block "presets" after "eq" in the HTML: kept right after eq in the user's order
  const withNew = ["player", "spectrum", "audio", "eq", "presets", "bluetooth"];
  assert.deepEqual(L.resolveOrder(saved, withNew), ["eq", "presets", "player", "spectrum", "audio", "bluetooth"]);
  // new first block: at the top
  assert.deepEqual(L.resolveOrder(saved, ["clock", ...DEFAULT]), ["clock", ...saved]);
  // several new blocks in a row keep their relative order
  assert.deepEqual(L.resolveOrder(["bluetooth", "player"], ["player", "a", "b", "bluetooth"]),
                   ["bluetooth", "player", "a", "b"]);
  const r = L.loadOrder(stored(saved), withNew);
  assert.equal(r.saved, true);
  assert.equal(r.repaired, true);                                   // rewritten with the new block
});

test("a block removed from the page disappears from the order", () => {
  assert.deepEqual(L.resolveOrder(["eq", "player", "spectrum", "audio", "bluetooth"], ["player", "audio", "eq"]),
                   ["eq", "player", "audio"]);
});

test("reset: the key is removed, the default order comes back", () => {
  const s = stored(["eq", "player", "spectrum", "audio", "bluetooth"]);
  assert.equal(L.clearOrder(s), true);
  assert.equal(s.getItem(L.STORAGE_KEY), null);
  assert.deepEqual(L.loadOrder(s, DEFAULT).order, DEFAULT);
});

test("storage blocked or full: default order, no exception", () => {
  assert.deepEqual(L.loadOrder(throwingStorage, DEFAULT).order, DEFAULT);
  assert.equal(L.saveOrder(throwingStorage, DEFAULT), false);
  assert.equal(L.clearOrder(throwingStorage), false);
  assert.equal(L.saveOrder(null, DEFAULT), false);
});

test("drop index: number of other blocks whose middle is above the pointer", () => {
  const middles = [100, 300, 500, 700];
  assert.equal(L.dropIndex(middles, 50), 0);
  assert.equal(L.dropIndex(middles, 299), 1);
  assert.equal(L.dropIndex(middles, 301), 2);
  assert.equal(L.dropIndex(middles, 900), 4);
  assert.equal(L.dropIndex([], 10), 0);
});

test("sameOrder", () => {
  assert.equal(L.sameOrder(DEFAULT, [...DEFAULT]), true);
  assert.equal(L.sameOrder(DEFAULT, L.moveBy(DEFAULT, "eq", 1)), false);
  assert.equal(L.sameOrder(DEFAULT, DEFAULT.slice(1)), false);
});
