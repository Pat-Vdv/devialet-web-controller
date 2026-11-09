"use strict";

// Spectrum analyzer display settings (V2.3.1), pure and DOM-free so they can be
// unit tested with node: themes, persisted preferences, visual sensitivity,
// peak hold durations, colour helpers and a Fullscreen API controller.
// Nothing here touches the audio data source: sensitivity only shifts what
// is drawn.
(function (root) {
  const STORAGE_KEY = "devialet.spectrum";

  // Theme colours live in CSS variables (app.css, [data-theme=...]); this list
  // only declares the available names. Adding a theme = one CSS block + one entry.
  const THEMES = Object.freeze({
    technics: "Technics",
    pioneer: "Pioneer",
    amber: "Vintage Amber",
  });
  const THEME_VARS = Object.freeze([
    "--spec-bg", "--spec-seg-low", "--spec-seg-high", "--spec-seg-off",
    "--spec-peak", "--spec-peak-glow", "--spec-grid", "--spec-label", "--spec-accent",
  ]);

  const SENSITIVITY_MIN = -12;
  const SENSITIVITY_MAX = 12;
  const PEAK_HOLD_OPTIONS = Object.freeze([500, 1000, 2000]);

  const DEFAULTS = Object.freeze({
    enabled: false,
    theme: "technics",
    sensitivity: 0,
    peakHold: true,
    peakHoldMs: 1000,
    mode: "stereo",
  });

  // Keep only known keys with valid values (so e.g. a stray "fullscreen" key
  // is never restored).
  function sanitize(raw) {
    const p = Object.assign({}, DEFAULTS);
    if (!raw || typeof raw !== "object") return p;
    if (typeof raw.enabled === "boolean") p.enabled = raw.enabled;
    if (typeof raw.theme === "string" && Object.prototype.hasOwnProperty.call(THEMES, raw.theme)) {
      p.theme = raw.theme;
    }
    const s = Number(raw.sensitivity);
    if (raw.sensitivity !== null && raw.sensitivity !== "" && Number.isFinite(s)) {
      p.sensitivity = Math.round(Math.min(SENSITIVITY_MAX, Math.max(SENSITIVITY_MIN, s)));
    }
    if (typeof raw.peakHold === "boolean") p.peakHold = raw.peakHold;
    if (PEAK_HOLD_OPTIONS.indexOf(Number(raw.peakHoldMs)) >= 0) p.peakHoldMs = Number(raw.peakHoldMs);
    if (raw.mode === "stereo" || raw.mode === "mono") p.mode = raw.mode;
    return p;
  }

  function load(storage) {
    try {
      const text = storage && storage.getItem(STORAGE_KEY);
      return sanitize(text ? JSON.parse(text) : null);
    } catch (e) {
      return sanitize(null);  // blocked storage or corrupt JSON: defaults
    }
  }

  function save(storage, prefs) {
    try {
      storage.setItem(STORAGE_KEY, JSON.stringify(sanitize(prefs)));
      return true;
    } catch (e) {
      return false;  // private mode / quota: preferences just are not kept
    }
  }

  // Visual sensitivity: shift every band by `db` for display only.
  function applySensitivity(dbValues, db) {
    const offset = Number.isFinite(db) ? db : 0;
    return Array.from(dbValues, (v) => (typeof v === "number" ? v + offset : v));
  }

  function formatSensitivity(db) {
    return db > 0 ? `+${db} dB` : db < 0 ? `−${-db} dB` : "0 dB";
  }

  // "#19e1ff" / "#1ef" / "rgb(25, 225, 255)" -> [25, 225, 255]
  function parseColor(text, fallback) {
    const s = String(text || "").trim();
    let m = /^#([0-9a-f]{6})$/i.exec(s);
    if (m) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
    m = /^#([0-9a-f]{3})$/i.exec(s);
    if (m) return [0, 1, 2].map((i) => parseInt(m[1][i] + m[1][i], 16));
    m = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i.exec(s);
    if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
    return fallback || [255, 255, 255];
  }

  function mixColor(a, b, t) {
    const k = Math.min(1, Math.max(0, t));
    return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * k)).join(",")})`;
  }

  // Palette read from CSS variables through getVar(name) -> string.
  function readPalette(getVar) {
    const get = (name, fallback) => (getVar(name) || "").trim() || fallback;
    return {
      low: parseColor(get("--spec-seg-low", "#19e1ff")),
      high: parseColor(get("--spec-seg-high", "#46ff9b")),
      off: get("--spec-seg-off", "rgba(40,210,235,0.07)"),
      peak: get("--spec-peak", "#ffffff"),
      peakGlow: get("--spec-peak-glow", "rgba(200,255,240,0.9)"),
      grid: get("--spec-grid", "rgba(90,140,150,0.14)"),
      label: get("--spec-label", "#5f7f84"),
      accent: get("--spec-accent", "#3fe7ff"),
    };
  }

  // Fullscreen API controller with vendor-prefix fallback and error reporting.
  // enter()/exit()/toggle() resolve to {ok: boolean, error?: string}.
  function createFullscreen(element, doc) {
    const request = element && (element.requestFullscreen || element.webkitRequestFullscreen);
    const exitFn = doc && (doc.exitFullscreen || doc.webkitExitFullscreen);
    const current = () => (doc && (doc.fullscreenElement || doc.webkitFullscreenElement)) || null;

    function supported() {
      return typeof request === "function" &&
        (doc.fullscreenEnabled !== false || doc.webkitFullscreenEnabled === true);
    }

    async function enter() {
      if (!supported()) return { ok: false, error: "Plein écran non pris en charge par ce navigateur" };
      if (current() === element) return { ok: true };
      try {
        await request.call(element);
        return { ok: true };
      } catch (e) {
        return { ok: false, error: `Plein écran refusé : ${(e && e.message) || e}` };
      }
    }

    async function exit() {
      if (!current() || typeof exitFn !== "function") return { ok: true };
      try {
        await exitFn.call(doc);
        return { ok: true };
      } catch (e) {
        return { ok: false, error: `Sortie du plein écran impossible : ${(e && e.message) || e}` };
      }
    }

    return {
      supported,
      active: () => current() === element,
      enter,
      exit,
      toggle: () => (current() === element ? exit() : enter()),
    };
  }

  const api = {
    STORAGE_KEY, THEMES, THEME_VARS, SENSITIVITY_MIN, SENSITIVITY_MAX, PEAK_HOLD_OPTIONS, DEFAULTS,
    sanitize, load, save, applySensitivity, formatSensitivity, parseColor, mixColor, readPalette,
    createFullscreen,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SpectrumSettings = api;
})(typeof window !== "undefined" ? window : globalThis);
