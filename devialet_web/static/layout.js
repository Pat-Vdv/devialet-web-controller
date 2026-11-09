"use strict";

// Movable console blocks (V2.4.3-B3.1, header drag since B3.1.2). A block
// (#blocks > [data-block]) is moved by dragging the free area of its header
// ([data-block-head]): with a mouse, as soon as it moves a few pixels; on a
// touch screen, after a short press and hold, so that a swipe still scrolls
// the page. Controls (buttons, sliders, knobs, lists, fields, links, anything
// focusable) never start a drag, and the block body never does. Keyboard and
// screen readers: Monter / Descendre buttons, hidden until they get the focus.
// The existing nodes are moved, never rebuilt: their state, listeners, timers
// and canvas are kept. The order is saved in localStorage (LayoutCore) and
// restored here, before the first paint (this script follows the blocks).
(function () {
  const L = window.LayoutCore;
  const container = document.getElementById("blocks");
  if (!container || !L) return;

  const storage = (() => { try { return window.localStorage; } catch (e) { return null; } })();
  const resetBtn = document.getElementById("layout-reset");
  const statusEl = document.getElementById("layout-status");
  const DRAG_START_PX = 6;       // mouse: a click on the header moves nothing
  const HOLD_MS = 300;           // touch / pen: press and hold before the block follows
  const HOLD_SLOP_PX = 8;        // moving more before that is a scroll, left to the browser
  // Never a drag from these (nor from inside them), wherever they sit in a header.
  const INTERACTIVE = "a[href], button, input, select, textarea, label, summary, details, output, "
    + "[role=slider], [role=button], [role=switch], [role=checkbox], [contenteditable]:not([contenteditable=false]), "
    + "[tabindex]";
  const EDGE_PX = 64;            // auto-scroll zone at the top / bottom of the window
  const FLIP_MS = 200;
  const ICONS = {
    up: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M6 2 11 9H1z"/></svg>',
    down: '<svg viewBox="0 0 12 12" aria-hidden="true"><path d="M6 10 1 3h10z"/></svg>',
  };

  const blocks = () => [...container.children].filter((el) => el.dataset.block);
  const ids = () => blocks().map((el) => el.dataset.block);
  const nodes = new Map(blocks().map((el) => [el.dataset.block, el]));
  const DEFAULT = ids();
  const label = (el) => el.dataset.blockLabel || el.dataset.block;
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const marker = document.createElement("div");
  marker.className = "drop-marker";
  marker.setAttribute("aria-hidden", "true");
  container.append(marker);
  let drag = null;

  function announce(text) {
    statusEl.textContent = "";
    setTimeout(() => { statusEl.textContent = text; }, 40);   // emptied first: same text announced again
  }

  // Put the blocks in `order`, moving only the misplaced nodes; with
  // `animate`, each moved block slides from where it was seen (FLIP),
  // including the dragged one from where it was dropped.
  function place(order, { animate = false, dragged = null } = {}) {
    const flip = animate && !reduced();
    const before = flip ? new Map(blocks().map((el) => [el, el.getBoundingClientRect().top])) : null;
    if (dragged) { dragged.classList.remove("dragging"); dragged.style.transform = ""; }
    const focused = document.activeElement;
    order.forEach((id, i) => {
      const node = nodes.get(id);
      const at = blocks()[i];
      if (node === at) return;
      if (typeof container.moveBefore === "function") container.moveBefore(node, at);  // keeps focus
      else container.insertBefore(node, at);
    });
    if (focused && focused !== document.activeElement && container.contains(focused)) {
      focused.focus({ preventScroll: true });
    }
    if (!flip) return;
    const moved = [];
    for (const [el, top] of before) {
      const dy = top - el.getBoundingClientRect().top;
      if (Math.abs(dy) < 1) continue;
      el.style.transition = "none";
      el.style.transform = `translateY(${dy}px)`;
      moved.push(el);
    }
    if (!moved.length) return;
    container.getBoundingClientRect();           // commit the inverted positions
    for (const el of moved) {
      el.style.transition = `transform ${FLIP_MS}ms ease-out`;
      el.style.transform = "";
      setTimeout(() => { if (el.style.transform === "") el.style.transition = ""; }, FLIP_MS + 50);
    }
  }

  // Apply and save; the default order is stored as "nothing saved" so that a
  // block added to the page later keeps its place from the HTML.
  function commit(order, dragged) {
    const changed = !L.sameOrder(order, ids());
    place(order, { animate: true, dragged });
    if (changed) {
      if (L.sameOrder(order, DEFAULT)) L.clearOrder(storage);
      else L.saveOrder(storage, order);
    }
    sync();
    return changed;
  }

  function sync() {
    const order = ids();
    resetBtn.disabled = L.sameOrder(order, DEFAULT);
    order.forEach((id, i) => {
      const el = nodes.get(id);
      el.querySelector('[data-step="-1"]').setAttribute("aria-disabled", String(i === 0));
      el.querySelector('[data-step="1"]').setAttribute("aria-disabled", String(i === order.length - 1));
    });
  }

  function moved(el) {
    const order = ids();
    announce(`« ${label(el)} » en position ${order.indexOf(el.dataset.block) + 1} sur ${order.length}`);
  }

  // Keyboard and buttons: one place up or down, or to the first / last place.
  function moveTo(el, index) {
    if (drag || document.fullscreenElement) return;
    if (!commit(L.move(ids(), el.dataset.block, index))) return;
    moved(el);
    setTimeout(() => el.scrollIntoView({ block: "nearest", behavior: reduced() ? "auto" : "smooth" }),
               reduced() ? 0 : FLIP_MS);
  }
  const step = (el, delta) => moveTo(el, ids().indexOf(el.dataset.block) + delta);

  // --- dragging -----------------------------------------------------------------------
  function update() {
    const y = drag.clientY + window.scrollY;
    drag.el.style.transform = `translateY(${y - drag.startY}px)`;
    const others = blocks().filter((b) => b !== drag.el);
    const rects = others.map((b) => b.getBoundingClientRect());
    drag.index = L.dropIndex(rects.map((r) => r.top + r.height / 2 + window.scrollY), y);
    const gap = parseFloat(getComputedStyle(container).rowGap) || 16;
    const top = container.getBoundingClientRect().top;
    const at = drag.index < rects.length ? rects[drag.index].top - gap / 2 : rects[rects.length - 1].bottom + gap / 2;
    marker.style.transform = `translateY(${Math.round(at - top)}px)`;
    marker.classList.toggle("same", drag.index === drag.from);
  }

  function autoscroll() {
    if (!drag || !drag.active) return;
    const y = drag.clientY;
    const h = window.innerHeight;
    const speed = y < EDGE_PX ? y - EDGE_PX : y > h - EDGE_PX ? y - (h - EDGE_PX) : 0;
    if (speed) { window.scrollBy(0, Math.round(speed / 3)); update(); }
    drag.timer = setTimeout(autoscroll, 16);   // ~60 Hz, also while frames are throttled
  }

  function begin() {
    drag.active = true;
    drag.from = ids().indexOf(drag.el.dataset.block);
    drag.el.classList.remove("lifting");
    drag.el.classList.add("dragging");
    document.documentElement.classList.add("layout-dragging");
    marker.classList.add("on");
    drag.timer = setTimeout(autoscroll, 16);
  }

  function finish(drop) {
    if (!drag) return;
    const { el, active, index, head, pointer } = drag;
    clearTimeout(drag.timer);
    clearTimeout(drag.hold);
    drag = null;
    if (head.hasPointerCapture(pointer)) head.releasePointerCapture(pointer);
    marker.classList.remove("on", "same");
    el.classList.remove("lifting");
    document.documentElement.classList.remove("layout-dragging");
    if (!active) return;
    const order = ids().filter((id) => id !== el.dataset.block);
    if (drop) order.splice(index, 0, el.dataset.block);
    if (commit(drop ? order : ids(), el)) moved(el);
    else announce(drop ? `« ${label(el)} » : position inchangée` : "Déplacement annulé");
  }

  function attach(el) {
    const name = label(el);
    const head = el.querySelector("[data-block-head]");
    head.title = "Glisser l'en-tête pour déplacer le bloc";
    const tools = document.createElement("span");
    tools.className = "block-tools";
    tools.setAttribute("role", "group");
    tools.setAttribute("aria-label", `Position du bloc « ${name} »`);
    const make = (text, icon, delta) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "block-step";
      b.dataset.step = String(delta);
      b.setAttribute("aria-label", text);
      b.setAttribute("aria-describedby", "layout-help");
      b.innerHTML = icon;
      b.addEventListener("click", () => {
        if (b.getAttribute("aria-disabled") !== "true") step(el, delta);
      });
      return b;
    };
    tools.append(make(`Monter « ${name} »`, ICONS.up, -1), make(`Descendre « ${name} »`, ICONS.down, 1));
    head.prepend(tools);
    tools.addEventListener("keydown", (e) => {
      const to = { ArrowUp: -1, ArrowDown: 1 }[e.key];
      if (to) step(el, to);
      else if (e.key === "Home") moveTo(el, 0);
      else if (e.key === "End") moveTo(el, DEFAULT.length - 1);
      else return;
      e.preventDefault();
    });

    head.addEventListener("pointerdown", (e) => {
      if (drag || e.button !== 0 || !e.isPrimary || document.fullscreenElement) return;
      const control = e.target.closest(INTERACTIVE);
      if (control && head.contains(control)) return;   // the controls keep their own behaviour
      const mouse = e.pointerType === "mouse";
      if (mouse) e.preventDefault();               // no text selection; touch keeps its scrolling
      try { head.setPointerCapture(e.pointerId); } catch (err) { /* pointer already gone */ }
      drag = { el, head, mouse, pointer: e.pointerId, startY: e.clientY + window.scrollY, clientY: e.clientY,
               active: false, armed: mouse, index: 0, from: 0, timer: 0, hold: 0 };
      if (!mouse) {
        drag.hold = setTimeout(() => {             // held still: the block is picked up
          if (!drag || drag.active) return;
          drag.armed = true;
          el.classList.add("lifting");
          if (navigator.vibrate) { try { navigator.vibrate(8); } catch (err) { /* not allowed */ } }
        }, HOLD_MS);
      }
    });
    head.addEventListener("pointermove", (e) => {
      if (!drag || e.pointerId !== drag.pointer) return;
      drag.clientY = e.clientY;
      if (!drag.active) {
        const moved = Math.abs(e.clientY + window.scrollY - drag.startY);
        if (!drag.armed) {                         // touch before the hold: a scroll, not a move
          if (moved > HOLD_SLOP_PX) finish(false);
          return;
        }
        if (drag.mouse && moved < DRAG_START_PX) return;
        begin();
      }
      update();
    });
    // Once the block is picked up, the page must not scroll under the finger:
    // touchmove is cancelled from then on only (passive: false is required).
    head.addEventListener("touchmove", (e) => { if (drag && drag.armed) e.preventDefault(); }, { passive: false });
    head.addEventListener("contextmenu", (e) => { if (drag && !drag.mouse) e.preventDefault(); });
    head.addEventListener("pointerup", (e) => { if (drag && e.pointerId === drag.pointer) finish(true); });
    head.addEventListener("pointercancel", () => finish(false));   // e.g. the browser started scrolling
    head.addEventListener("lostpointercapture", () => finish(false));
  }

  // --- start --------------------------------------------------------------------------
  blocks().forEach(attach);
  const { order, saved, repaired } = L.loadOrder(storage, DEFAULT);
  place(order);
  if (repaired) {                                  // unknown ids dropped, new blocks inserted
    if (saved && !L.sameOrder(order, DEFAULT)) L.saveOrder(storage, order);
    else L.clearOrder(storage);
  }
  sync();

  resetBtn.addEventListener("click", () => {
    commit(DEFAULT);
    announce("Disposition d'origine restaurée");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && drag && drag.active) { e.preventDefault(); finish(false); }
  });
  window.addEventListener("storage", (e) => {      // another tab changed the layout
    if (e.key !== L.STORAGE_KEY && e.key !== null) return;
    if (drag) finish(false);
    place(L.loadOrder(storage, DEFAULT).order, { animate: true });
    sync();
  });
})();
