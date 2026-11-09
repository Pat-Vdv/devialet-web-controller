"use strict";

// Tone control (V2.4.1) logic, pure and DOM-free so it can be unit tested with
// node: value formatting, knob geometry, keyboard / mouse / wheel mapping and
// a write coalescer. The coalescer keeps at most one request in flight and
// merges what the user did meanwhile into the next one (latest value wins),
// so dragging a knob never sends one request per pixel.
(function (root) {
  const MIN = -6;
  const MAX = 6;
  const SWEEP = 270;            // degrees, from -135 (min) to +135 (max)
  const PX_PER_DB = 14;         // drag distance for 1 dB
  const SEND_INTERVAL_MS = 200; // minimum spacing between two writes
  const BANDS = Object.freeze(["bass", "treble"]);

  function clampGain(value, min = MIN, max = MAX) {
    const n = typeof value === "string" && value.trim() === "" ? NaN : Number(value);
    if (value === null || typeof value === "boolean" || !Number.isFinite(n)) return null;
    return Math.max(min, Math.min(max, Math.round(n)));
  }

  function formatDb(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "— dB";
    const v = Number(value);
    if (v === 0) return "0 dB";
    const text = String(Math.abs(v)).replace(".", ",");
    return `${v > 0 ? "+" : "−"}${text} dB`;
  }

  function valueToAngle(value, min = MIN, max = MAX) {
    const v = Math.max(min, Math.min(max, Number(value) || 0));
    return -SWEEP / 2 + ((v - min) / (max - min)) * SWEEP;
  }

  // Keyboard, following the WAI-ARIA slider pattern. Returns null for keys we
  // do not handle (Tab, letters…) so the page keeps its normal behaviour.
  function keyToValue(key, value, min = MIN, max = MAX) {
    const v = clampGain(value, min, max) ?? 0;
    switch (key) {
      case "ArrowUp": case "ArrowRight": return Math.min(max, v + 1);
      case "ArrowDown": case "ArrowLeft": return Math.max(min, v - 1);
      case "PageUp": return Math.min(max, v + 3);
      case "PageDown": return Math.max(min, v - 3);
      case "Home": return min;
      case "End": return max;
      case "0": case "Delete": case "Backspace": return 0;
      default: return null;
    }
  }

  // Drag up or right to raise, down or left to lower.
  function dragToValue(startValue, dx, dy, min = MIN, max = MAX, pxPerDb = PX_PER_DB) {
    return clampGain(startValue + Math.trunc((dx - dy) / pxPerDb), min, max);
  }

  function wheelToValue(value, deltaY, min = MIN, max = MAX) {
    if (!deltaY) return clampGain(value, min, max);
    return clampGain((clampGain(value, min, max) ?? 0) + (deltaY < 0 ? 1 : -1), min, max);
  }

  // One knob's interaction state. `onChange(value)` fires only when the value
  // actually changes; the DOM layer forwards it to the coalescer.
  function createKnob({ min = MIN, max = MAX, value = 0, onChange = () => {}, pxPerDb = PX_PER_DB } = {}) {
    let current = clampGain(value, min, max) ?? 0;
    let drag = null;
    let disabled = false;
    function set(next) {
      const v = clampGain(next, min, max);
      if (v === null || v === current || disabled) return false;
      current = v;
      onChange(v);
      return true;
    }
    return {
      get value() { return current; },
      get dragging() { return drag !== null; },
      get disabled() { return disabled; },
      setDisabled(flag) { disabled = !!flag; if (disabled) drag = null; },
      // Value read from the speakers: never overrides a drag in progress.
      sync(v) {
        const c = clampGain(v, min, max);
        if (c !== null && drag === null) current = c;
      },
      keydown(key) {
        if (disabled) return false;
        const next = keyToValue(key, current, min, max);
        if (next === null) return false;
        set(next);
        return true; // handled (even at a bound): caller prevents the default scroll
      },
      pointerDown(x, y) { if (!disabled) drag = { x, y, start: current }; },
      pointerMove(x, y) {
        if (drag) set(dragToValue(drag.start, x - drag.x, y - drag.y, min, max, pxPerDb));
      },
      pointerUp() { drag = null; },
      wheel(deltaY) { return set(wheelToValue(current, deltaY, min, max)); },
      reset() { return set(0); },  // double-click
    };
  }

  // Write coalescer. `send(body)` posts {bass?, treble?} and resolves to the
  // server state (values read back from the speakers) or rejects. `bands` and
  // `normalize` let the graphic equalizer (V2.4.3-B) reuse it for its 10 bands.
  function createToneSync({
    send, onState = () => {}, onError = () => {}, onActivity = () => {},
    bands = BANDS, normalize = (v) => clampGain(v),
    intervalMs = SEND_INTERVAL_MS, now = () => Date.now(),
    setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (t) => clearTimeout(t),
  }) {
    let pending = {};
    let inFlight = null;
    let timer = null;
    let lastSent = -Infinity;

    const hasPending = () => Object.keys(pending).length > 0;
    const activity = () => onActivity({ pending: { ...pending }, sending: inFlight !== null });

    function schedule() {
      if (inFlight || timer !== null || !hasPending()) return;
      const wait = Math.max(0, lastSent + intervalMs - now());
      timer = setTimer(() => { timer = null; flush(); }, wait);
    }

    function flush() {
      if (inFlight || !hasPending()) return inFlight || Promise.resolve();
      const body = pending;
      pending = {};
      lastSent = now();
      inFlight = Promise.resolve()
        .then(() => send(body))
        .then((state) => onState(state, body), (err) => onError(err, body))
        .finally(() => { inFlight = null; activity(); schedule(); });
      activity();
      return inFlight;
    }

    return {
      submit(band, value) {
        if (!bands.includes(band)) throw new Error(`bande inconnue : ${band}`);
        const v = normalize(value);
        if (v === null) return;
        pending[band] = v;
        activity();
        schedule();
      },
      busy: () => inFlight !== null || timer !== null || hasPending(),
      pending: () => ({ ...pending }),
      // Resolves once everything submitted so far has been sent and answered.
      async idle() {
        while (inFlight || timer !== null || hasPending()) {
          if (timer !== null) { clearTimer(timer); timer = null; }
          await (inFlight || flush());
        }
      },
    };
  }

  const api = {
    MIN, MAX, SWEEP, PX_PER_DB, SEND_INTERVAL_MS, BANDS,
    clampGain, formatDb, valueToAngle, keyToValue, dragToValue, wheelToValue,
    createKnob, createToneSync,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ToneCore = api;
})(typeof window !== "undefined" ? window : globalThis);
