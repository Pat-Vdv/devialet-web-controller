"use strict";

// Console layout (V2.4.3-B3.1), pure and DOM-free so it can be unit tested
// with node: order of the movable blocks, saved in localStorage, repaired
// against the blocks actually on the page, and the drop position of a drag.
// The default order is the HTML order: a block added to index.html later is
// known without touching this file, and a saved order made before it exists
// gets it back next to its default neighbour.
(function (root) {
  const STORAGE_KEY = "devialet.layout";
  const VERSION = 1;

  const isId = (value) => typeof value === "string" && /^[a-z][a-z0-9-]{0,31}$/.test(value);

  // Saved order -> full order of the known blocks: unknown or duplicate ids
  // are dropped, missing blocks are put back after the block that precedes
  // them in the default order (at the start when none does).
  function resolveOrder(saved, defaults) {
    const known = new Set(defaults);
    const order = [];
    for (const id of Array.isArray(saved) ? saved : []) {
      if (known.has(id) && !order.includes(id)) order.push(id);
    }
    defaults.forEach((id, i) => {
      if (order.includes(id)) return;
      const before = defaults.slice(0, i).reverse().find((d) => order.includes(d));
      order.splice(before === undefined ? 0 : order.indexOf(before) + 1, 0, id);
    });
    return order;
  }

  const sameOrder = (a, b) => a.length === b.length && a.every((id, i) => id === b[i]);

  function read(storage) {
    try { return storage ? storage.getItem(STORAGE_KEY) : null; } catch (e) { return null; }
  }

  // {order, saved, repaired}: `saved` false when nothing usable was stored,
  // `repaired` when the stored order had to be changed to fit the page.
  function loadOrder(storage, defaults) {
    const raw = read(storage);
    let ids = null;
    if (raw !== null) {
      try {
        const data = JSON.parse(raw);
        const list = Array.isArray(data) ? data : data && data.order;   // bare array tolerated
        if (Array.isArray(list)) ids = list.filter(isId);
      } catch (e) { /* corrupt: default order */ }
    }
    if (!ids) return { order: [...defaults], saved: false, repaired: raw !== null };
    const order = resolveOrder(ids, defaults);
    return { order, saved: true, repaired: !sameOrder(order, ids) };
  }

  function saveOrder(storage, order) {
    try { storage.setItem(STORAGE_KEY, JSON.stringify({ version: VERSION, order })); return true; }
    catch (e) { return false; }
  }

  // Back to the default: the key is removed so that blocks added later keep
  // their place from the HTML.
  function clearOrder(storage) {
    try { storage.removeItem(STORAGE_KEY); return true; } catch (e) { return false; }
  }

  function move(order, id, index) {
    const from = order.indexOf(id);
    if (from < 0) return [...order];
    const rest = order.filter((x) => x !== id);
    rest.splice(Math.max(0, Math.min(rest.length, index)), 0, id);
    return rest;
  }

  const moveBy = (order, id, delta) => move(order, id, order.indexOf(id) + delta);

  // Drop index among the other blocks (dragged one removed) for a pointer at
  // `y`: the number of other blocks whose middle is above it.
  function dropIndex(middles, y) {
    return middles.filter((m) => m < y).length;
  }

  const api = {
    STORAGE_KEY, VERSION,
    resolveOrder, sameOrder, loadOrder, saveOrder, clearOrder, move, moveBy, dropIndex,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.LayoutCore = api;
})(typeof window !== "undefined" ? window : globalThis);
