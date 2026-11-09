"use strict";

// Tone panel (V2.4.1): two rotary knobs, BASS and TREBLE, wired to /api/tone.
// The digital readouts only ever show values read back from the Phantom; the
// knob position is the local target while a change is being sent.
(function () {
  const T = window.ToneCore;
  const POLL_MS = 3000;
  const LABELS = { bass: "Basses", treble: "Aigus" };
  const $ = (id) => document.getElementById(id);
  const card = $("tone-card");
  if (!card || !T) return;

  const els = { pill: $("tone-pill"), message: $("tone-message"), neutral: $("tone-neutral") };
  let status = null;        // last server state
  let lastError = "";
  let pollTimer = null;
  const knobs = {};

  async function api(method, body) {
    const res = await fetch("/api/tone", {
      method,
      headers: body ? { "Content-Type": "application/json", "X-Devialet-Request": "1" } : { "X-Devialet-Request": "1" },  // see security.py
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      const text = Array.isArray(d) ? "valeur refusée"
        : typeof d === "string" ? d : d?.message || d?.code || `HTTP ${res.status}`;
      throw new Error(text);
    }
    return data;
  }

  const sync = T.createToneSync({
    send: (body) => api("POST", body),
    onState(state) {
      lastError = state.confirmed === false ? "Réglage envoyé mais non confirmé par l'enceinte" : "";
      apply(state);
    },
    onError(err) {
      lastError = `Échec : ${err.message || err}. Valeurs réelles relues.`;
      refresh();             // re-read: the knobs fall back to what the speakers report
    },
    onActivity: render,
  });

  // SVG scale: 13 ticks (-6..+6), labels every 3 dB.
  function buildScale(svg) {
    const ns = "http://www.w3.org/2000/svg";
    for (let v = T.MIN; v <= T.MAX; v++) {
      const a = (T.valueToAngle(v) - 90) * Math.PI / 180;
      const major = v % 3 === 0;
      const r1 = major ? 70 : 73, r2 = 79;
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", 90 + r1 * Math.cos(a)); line.setAttribute("y1", 90 + r1 * Math.sin(a));
      line.setAttribute("x2", 90 + r2 * Math.cos(a)); line.setAttribute("y2", 90 + r2 * Math.sin(a));
      line.setAttribute("class", `tick${major ? " major" : ""}`);
      line.dataset.value = String(v);
      svg.append(line);
      if (major) {
        const text = document.createElementNS(ns, "text");
        text.setAttribute("x", 90 + 88 * Math.cos(a)); text.setAttribute("y", 90 + 88 * Math.sin(a));
        text.textContent = v === 0 ? "0" : (v > 0 ? `+${v}` : `−${-v}`);
        svg.append(text);
      }
    }
  }

  function setupKnob(band) {
    const root = card.querySelector(`[data-band="${band}"]`);
    const el = root.querySelector(".tone-knob");
    const knob = T.createKnob({ onChange: (v) => { sync.submit(band, v); render(); } });
    knob.setDisabled(true);
    buildScale(root.querySelector(".tone-scale"));
    knobs[band] = { knob, root, el, readout: root.querySelector(".tone-readout"),
      note: root.querySelector(".tone-note"), pointer: root.querySelector(".tone-pointer") };

    el.addEventListener("keydown", (e) => {
      if (e.altKey || e.ctrlKey || e.metaKey) return;
      if (knob.keydown(e.key)) e.preventDefault();
    });
    el.addEventListener("pointerdown", (e) => {
      if (knob.disabled || e.button !== 0) return;
      el.focus();
      el.setPointerCapture(e.pointerId);
      knob.pointerDown(e.clientX, e.clientY);
      render();
    });
    el.addEventListener("pointermove", (e) => knob.pointerMove(e.clientX, e.clientY));
    const end = () => { knob.pointerUp(); render(); };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.addEventListener("lostpointercapture", end);
    el.addEventListener("dblclick", (e) => { e.preventDefault(); knob.reset(); });
    // The wheel only acts on the focused knob, so scrolling the page never changes the tone.
    el.addEventListener("wheel", (e) => {
      if (document.activeElement !== el || knob.disabled) return;
      e.preventDefault();
      knob.wheel(e.deltaY);
    }, { passive: false });
  }

  function apply(state) {
    status = state;
    for (const band of T.BANDS) knobs[band].knob.setDisabled(!state.available);
    render();
  }

  function render() {
    const s = status;
    const online = !!(s && s.available);
    const pending = sync.pending();
    for (const band of T.BANDS) {
      const { knob, root, el, readout, note, pointer } = knobs[band];
      // Follow the speakers unless this knob is being turned or a change is queued.
      if (online && !sync.busy() && s[band] !== null) knob.sync(s[band]);
      const applied = s ? s[band] : null;
      pointer.style.transform = `rotate(${T.valueToAngle(knob.value)}deg)`;
      readout.textContent = online ? T.formatDb(applied) : "— dB";
      const target = band in pending ? pending[band] : knob.value;
      const waiting = online && (knob.dragging || band in pending || (sync.busy() && target !== applied));
      note.textContent = !online ? "" : waiting ? `Réglage ${T.formatDb(target)} · envoi…`
        : applied === 0 ? "Neutre" : "Appliqué";
      root.classList.toggle("waiting", waiting);
      root.classList.toggle("off", !online);
      for (const tick of root.querySelectorAll(".tick")) {
        tick.classList.toggle("lit", online && Number(tick.dataset.value) === applied);
      }
      el.setAttribute("aria-valuenow", String(knob.value));
      el.setAttribute("aria-valuetext", online
        ? `${T.formatDb(knob.value)}${waiting ? ", envoi en cours" : ""}, appliqué ${T.formatDb(applied)}`
        : "indisponible");
      el.setAttribute("aria-disabled", String(!online));
      el.tabIndex = online ? 0 : -1;
    }
    els.neutral.disabled = !online || (s.bass === 0 && s.treble === 0 && !sync.busy());

    let pill = "pill-wait", label = "…";
    if (s && !online) { pill = "pill-bad"; label = "Indisponible"; }
    else if (online && sync.busy()) { label = "Envoi…"; }
    else if (online && lastError) { pill = "pill-bad"; label = "Erreur"; }
    else if (online) { pill = "pill-ok"; label = "Synchronisé"; }
    els.pill.className = `pill ${pill}`;
    els.pill.textContent = label;

    const lines = [];
    if (lastError) lines.push(lastError);
    if (s && s.errors && s.errors.length) lines.push(...s.errors);
    if (s && !online) lines.push("Nouvelle tentative automatique toutes les 3 s.");
    if (online && s.preset && !["custom", "flat"].includes(s.preset)) {
      lines.push(`Préréglage « ${s.preset} » actif : un réglage le remplacera.`);
    }
    els.message.textContent = [...new Set(lines)].join("\n");
  }

  async function refresh() {
    clearTimeout(pollTimer);
    try {
      const state = await api("GET");
      apply(state);
    } catch (err) {
      status = { available: false, errors: [`Serveur injoignable : ${err.message || err}`] };
      for (const band of T.BANDS) knobs[band].knob.setDisabled(true);
      render();
    }
    if (!document.hidden) pollTimer = setTimeout(poll, POLL_MS);
  }

  // Periodic re-read picks up changes made from the Devialet app.
  function poll() {
    const turning = T.BANDS.some((b) => knobs[b].knob.dragging);
    if (sync.busy() || turning) { pollTimer = setTimeout(poll, POLL_MS); return; }
    refresh();
  }

  T.BANDS.forEach(setupKnob);
  els.neutral.addEventListener("click", () => {
    for (const band of T.BANDS) knobs[band].knob.reset();
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  render();
  refresh();
})();
