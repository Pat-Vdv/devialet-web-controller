"use strict";

// Graphic equalizer (V2.4.3-B3) logic, pure and DOM-free so it can be unit
// tested with node: band list, gain normalisation, short readouts, fader
// scale and the panel status derived from /api/eq. Writes go through the
// tone coalescer (ToneCore.createToneSync) with these bands and this
// normalisation. Presets (B4) come from /api/eq; "Custom" recalls the last
// manual curve kept in devialet-eq.txt.
(function (root) {
  const BANDS = Object.freeze([31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]);
  const MIN = -12;
  const MAX = 12;
  const STEP = 0.5;
  const SCALE = Object.freeze([12, 6, 0, -6, -12]);      // labelled marks, top to bottom
  const KEYS = Object.freeze(BANDS.map(String));          // coalescer band keys

  function normalizeGain(value) {
    const n = typeof value === "string" && value.trim() === "" ? NaN : Number(value);
    if (value === null || typeof value === "boolean" || !Number.isFinite(n)) return null;
    return Math.max(MIN, Math.min(MAX, Math.round(n / STEP) * STEP));
  }

  function freqLabel(f) {
    return f >= 1000 ? `${f / 1000}k` : String(f);
  }

  function bandName(f) {
    return f >= 1000 ? `${f / 1000} kHz` : `${f} Hz`;
  }

  // Readout under a fader: "0", "+6", "−1,5" (unit shown once, in the header).
  function formatGain(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    const v = Number(value);
    if (v === 0) return "0";
    return `${v > 0 ? "+" : "−"}${String(Math.abs(v)).replace(".", ",")}`;
  }

  function formatDb(value) {
    const g = formatGain(value);
    return g === "—" ? "— dB" : `${g} dB`;
  }

  // Position of a gain on the fader travel, 0 (bottom) to 1 (top).
  function gainToFraction(value) {
    const v = Math.max(MIN, Math.min(MAX, Number(value) || 0));
    return (v - MIN) / (MAX - MIN);
  }

  // Panel state from the last /api/eq answer (null before the first one).
  // `editable`: faders usable; `bypassed`: settings kept but sound neutral;
  // `alert`: the message lines report a problem (otherwise plain information).
  function describe(state, { busy = false, error = "" } = {}) {
    const s = state || null;
    if (!s) return { pill: "pill-wait", label: "…", editable: false, bypassed: false, alert: false, lines: [] };
    const lines = [];
    if (error) lines.push(error);
    if (s.status !== "ok") {
      const labels = { missing: "Fichier absent", unmanaged: "Non géré", inconsistent: "Modifié à la main",
                       disabled: "Non configuré", unavailable: "Introuvable", server: "Hors ligne" };
      if (s.message) lines.push(s.message);
      if (s.status === "missing") lines.push("Le son n'est pas égalisé. « Réinitialiser » recrée un fichier neutre.");
      if (s.status === "unmanaged" || s.status === "inconsistent") {
        lines.push("Le contrôleur ne remplace pas ce fichier : voir le diagnostic (rapport V2.4.3-B).");
      }
      return { pill: "pill-bad", label: labels[s.status] || "Erreur", editable: false, bypassed: false,
               alert: true, canReset: s.status === "missing", lines };
    }
    const bypassed = s.enabled === false;
    if (bypassed) lines.push("Égaliseur désactivé : son neutre, réglages conservés.");
    let pill = "pill-ok", label = bypassed ? "Désactivé" : "Synchronisé";
    if (busy) { pill = "pill-wait"; label = "Envoi…"; }
    else if (error) { pill = "pill-bad"; label = "Erreur"; }
    else if (bypassed) pill = "pill-wait";
    const flat = Array.isArray(s.gains) && s.gains.every((g) => g === 0);
    return { pill, label, editable: true, bypassed, alert: !!error, canReset: !flat || bypassed, lines };
  }

  // Preset buttons from the last /api/eq answer: the fixed curves, then Custom.
  // Custom is a recall button, usable once a manual curve has been recorded.
  function presetButtons(state, { busy = false } = {}) {
    const s = state || {};
    const ok = s.status === "ok";
    const list = Array.isArray(s.presets) ? s.presets : [];
    const out = list.map((p) => ({
      id: p.id, label: p.label, pressed: ok && s.preset === p.id, disabled: !ok || busy,
      title: `${p.label} · préampli ${formatDb(p.preamp)}`,
    }));
    const custom = ok && Array.isArray(s.custom);
    out.push({
      id: "custom", label: "Custom", pressed: ok && s.preset === "custom", disabled: !ok || busy || !custom,
      title: custom ? "Rappeler le dernier réglage manuel" : "Aucun réglage manuel enregistré : déplacez un curseur",
    });
    return out;
  }

  // Text of the confirmation asked by the server (409 "confirm").
  function confirmText(detail) {
    const jump = String(detail && detail.jumpDb != null ? detail.jumpDb : "?").replace(".", ",");
    const limit = String(detail && detail.limitDb != null ? detail.limitDb : 6).replace(".", ",");
    return `Le niveau va monter jusqu'à +${jump} dB sur certaines fréquences (plus de ${limit} dB). ` +
      "Baissez le volume si nécessaire avant d'appliquer.";
  }

  const api = {
    BANDS, KEYS, MIN, MAX, STEP, SCALE,
    normalizeGain, freqLabel, bandName, formatGain, formatDb, gainToFraction, describe,
    presetButtons, confirmText,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.EqCore = api;
})(typeof window !== "undefined" ? window : globalThis);
