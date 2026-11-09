"use strict";

// Global theme (V2.4.2): one source of truth for the whole page.
// Loaded synchronously in <head> so the saved theme is on <html> before the
// first paint (no flash of the wrong theme). The analyzer derives its palette
// from the global theme (Classic keeps the Technics display).
// Pure helpers are exported for node tests; the browser part runs at load.
(function (root) {
  const STORAGE_KEY = "devialet.theme";
  const LEGACY_KEY = "devialet.spectrum";   // V2.3.1 analyzer prefs held the theme
  const THEMES = Object.freeze({
    classic: "Classic",
    technics: "Technics",
    pioneer: "Pioneer",
    amber: "Vintage Amber",
  });
  const DEFAULT_THEME = "technics";
  const EVENT = "devialet:themechange";
  const POWER_ON_MS = 1100;

  const has = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
  const isTheme = (value) => typeof value === "string" && has(THEMES, value);

  function sanitizeTheme(value) { return isTheme(value) ? value : DEFAULT_THEME; }

  // Analyzer palette (.spectrum[data-theme]) for a global theme.
  function spectrumPalette(theme) {
    const t = sanitizeTheme(theme);
    return t === "classic" ? "technics" : t;
  }

  function read(storage, key) {
    try { return storage ? storage.getItem(key) : null; } catch (e) { return null; }
  }

  // Saved theme, else the theme chosen in the V2.3.1 analyzer, else the default.
  function loadTheme(storage) {
    const saved = read(storage, STORAGE_KEY);
    if (isTheme(saved)) return { theme: saved, migrated: false };
    if (saved === null) {
      try {
        const legacy = JSON.parse(read(storage, LEGACY_KEY));
        if (legacy && isTheme(legacy.theme)) return { theme: legacy.theme, migrated: true };
      } catch (e) { /* corrupt legacy prefs: ignored */ }
    }
    return { theme: DEFAULT_THEME, migrated: false };
  }

  function saveTheme(storage, theme) {
    try { storage.setItem(STORAGE_KEY, sanitizeTheme(theme)); return true; } catch (e) { return false; }
  }

  function applyTheme(doc, theme) {
    const t = sanitizeTheme(theme);
    doc.documentElement.dataset.theme = t;
    const detail = { theme: t, palette: spectrumPalette(t) };
    if (typeof doc.dispatchEvent === "function" && typeof CustomEvent === "function") {
      doc.dispatchEvent(new CustomEvent(EVENT, { detail }));
    }
    return detail;
  }

  const currentTheme = (doc) => sanitizeTheme(doc.documentElement.dataset.theme);

  // The single theme control lives in the header; in fullscreen only the
  // analyzer is visible, so the same node is moved into it and back.
  function dock(control, home, fullscreenHost, active) {
    const target = active ? fullscreenHost : home;
    if (control && target && control.parentNode !== target) target.appendChild(control);
    return target;
  }

  function bindSelect(select, doc, storage) {
    select.replaceChildren();
    for (const [value, label] of Object.entries(THEMES)) {
      const option = doc.createElement("option");
      option.value = value;
      option.textContent = label;
      select.append(option);
    }
    select.value = currentTheme(doc);
    select.addEventListener("change", () => {
      saveTheme(storage, select.value);
      applyTheme(doc, select.value);
    });
    doc.addEventListener(EVENT, (e) => { select.value = e.detail.theme; });
  }

  // Runs once at page load: theme before paint, power-on animation (CSS,
  // skipped with prefers-reduced-motion), migration persisted.
  function boot(doc, storage) {
    const { theme, migrated } = loadTheme(storage);
    doc.documentElement.dataset.theme = theme;
    if (migrated) saveTheme(storage, theme);
    doc.documentElement.classList.add("power-on");
    setTimeout(() => doc.documentElement.classList.remove("power-on"), POWER_ON_MS);
    return theme;
  }

  const api = {
    STORAGE_KEY, LEGACY_KEY, THEMES, DEFAULT_THEME, EVENT, POWER_ON_MS,
    sanitizeTheme, spectrumPalette, loadTheme, saveTheme, applyTheme, currentTheme, dock,
    bindSelect, boot,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.DevialetTheme = api;
    const storage = (() => { try { return root.localStorage; } catch (e) { return null; } })();
    boot(document, storage);
    document.addEventListener("DOMContentLoaded", () => {
      const select = document.getElementById("theme-select");
      if (select) bindSelect(select, document, storage);
    });
  }
})(typeof window !== "undefined" ? window : globalThis);
