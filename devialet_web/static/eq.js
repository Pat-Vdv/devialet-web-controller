"use strict";

// Graphic equalizer panel (V2.4.3-B3): ten vertical faders wired to /api/eq.
// Equalizer APO does the processing on the Windows Bluetooth output; the
// backend only writes devialet-eq.txt. The readouts show the values read back
// from that file; a fader position is the local target while it is sent.
// Presets, reset and on/off (B4) may be answered 409 "confirm" when they would
// raise the level by more than 6 dB: the panel asks, then repeats the request
// with confirm and the revision the warning was computed on.
(function () {
  const E = window.EqCore;
  const T = window.ToneCore;
  const POLL_MS = 4000;
  const $ = (id) => document.getElementById(id);
  const card = $("eq-card");
  if (!card || !E || !T) return;

  const els = {
    pill: $("eq-pill"), board: $("eq-board"), power: $("eq-power"), powerLabel: $("eq-power-label"),
    preamp: $("eq-preamp"), reset: $("eq-reset"), message: $("eq-message"),
    presets: $("eq-presets"), confirm: $("eq-confirm"), confirmText: $("eq-confirm-text"),
    confirmOk: $("eq-confirm-ok"), confirmCancel: $("eq-confirm-cancel"),
  };
  let status = null;
  let lastError = "";
  let pollTimer = null;
  let actionBusy = false;          // enable / disable / reset in progress
  const bands = {};                // key -> {input, readout, root, dragging}
  const presetEls = {};            // preset id -> button
  let pendingConfirm = null;       // {path, body, opener} waiting for "Appliquer quand même"

  async function api(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json", "X-Devialet-Request": "1" } : { "X-Devialet-Request": "1" },  // see security.py
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data.detail;
      const text = Array.isArray(d) ? "valeur refusée"
        : typeof d === "string" ? d : d?.message || d?.code || `HTTP ${res.status}`;
      throw Object.assign(new Error(text), { status: res.status, detail: d });
    }
    return data;
  }

  const sync = T.createToneSync({
    send: (pending) => api("PATCH", "/api/eq/bands", { bands: pending }),
    bands: E.KEYS,
    normalize: E.normalizeGain,
    onState(state) { lastError = ""; apply(state); },
    onError(err) {
      lastError = `Échec : ${err.message || err}. Valeurs du fichier relues.`;
      refresh();
    },
    onActivity: render,
  });

  function build() {
    const scale = document.createElement("div");
    scale.className = "eq-scale";
    scale.setAttribute("aria-hidden", "true");
    const marks = document.createElement("div");
    marks.className = "eq-marks";
    for (const v of E.SCALE) {
      const m = document.createElement("span");
      m.style.bottom = `${E.gainToFraction(v) * 100}%`;
      m.textContent = v === 0 ? "0" : E.formatGain(v);
      marks.append(m);
    }
    const unit = document.createElement("span");
    unit.className = "eq-unit";
    unit.textContent = "dB";
    scale.append(marks, unit);
    els.board.append(scale);

    for (const f of E.BANDS) {
      const key = String(f);
      const root = document.createElement("div");
      root.className = "eq-band";
      root.dataset.freq = key;
      const slot = document.createElement("div");
      slot.className = "eq-slot";
      const input = document.createElement("input");
      Object.assign(input, { type: "range", min: E.MIN, max: E.MAX, step: E.STEP, value: 0, disabled: true });
      input.className = "eq-fader";
      input.setAttribute("aria-label", `Égaliseur ${E.bandName(f)}`);
      input.title = "Double-clic : 0 dB";
      slot.append(input);
      const readout = document.createElement("output");
      readout.className = "eq-readout";
      readout.textContent = "—";
      const name = document.createElement("span");
      name.className = "eq-freq";
      name.textContent = E.freqLabel(f);
      root.append(slot, readout, name);
      els.board.append(root);
      const band = { input, readout, root, dragging: false };
      bands[key] = band;

      input.addEventListener("input", () => { sync.submit(key, input.value); render(); });
      input.addEventListener("pointerdown", () => { band.dragging = true; });
      const end = () => { band.dragging = false; render(); };
      input.addEventListener("pointerup", end);
      input.addEventListener("pointercancel", end);
      input.addEventListener("blur", end);
      input.addEventListener("dblclick", () => {
        if (input.disabled) return;
        input.value = "0";
        sync.submit(key, 0);
        render();
      });
    }
  }

  function renderPresets(busy) {
    const list = E.presetButtons(status, { busy: busy || !!pendingConfirm });
    const known = new Set(list.map((p) => p.id));
    for (const [id, b] of Object.entries(presetEls)) {       // e.g. server unreachable: no list
      if (!known.has(id)) { b.disabled = true; b.setAttribute("aria-pressed", "false"); }
    }
    if (!Object.keys(presetEls).length && list.length < 2) return;   // wait for the server's list
    for (const p of list) {
      let b = presetEls[p.id];
      if (!b) {                    // built once, in the order of the first answer that lists them
        b = document.createElement("button");
        b.type = "button";
        b.className = "eq-preset";
        b.dataset.preset = p.id;
        b.textContent = p.label;
        b.addEventListener("click", () => {
          if (b.getAttribute("aria-pressed") !== "true") action("/api/eq/preset", { preset: p.id });
        });
        els.presets.append(b);
        presetEls[p.id] = b;
      }
      b.disabled = p.disabled;
      b.title = p.title;
      b.setAttribute("aria-pressed", String(p.pressed));
    }
  }

  function apply(state) {
    status = state;
    render();
  }

  function render() {
    const s = status;
    const busy = sync.busy() || actionBusy;
    const view = E.describe(s, { busy, error: lastError });
    const pending = sync.pending();
    const ok = !!(s && s.status === "ok");
    E.BANDS.forEach((f, i) => {
      const key = String(f);
      const { input, readout, root, dragging } = bands[key];
      const applied = ok ? s.gains[i] : null;
      // Follow the file unless this fader is held or a change is queued.
      if (ok && !sync.busy() && !dragging) input.value = String(applied);
      const target = key in pending ? pending[key] : E.normalizeGain(input.value);
      const waiting = ok && (dragging || key in pending || (sync.busy() && target !== applied));
      readout.textContent = ok ? E.formatGain(waiting ? target : applied) : "—";
      root.classList.toggle("waiting", waiting);
      root.classList.toggle("boost", ok && applied > 0);
      root.classList.toggle("cut", ok && applied < 0);
      input.disabled = !view.editable;
      input.setAttribute("aria-valuetext", ok
        ? `${E.formatDb(target)}${waiting ? ", envoi en cours" : ""}, appliqué ${E.formatDb(applied)}`
        : "indisponible");
    });
    card.classList.toggle("off", !view.editable);
    card.classList.toggle("bypassed", view.bypassed);

    els.power.disabled = !ok || busy;
    els.power.setAttribute("aria-pressed", String(ok && s.enabled === true));
    els.powerLabel.textContent = ok && s.enabled ? "Activé" : "Désactivé";
    els.reset.disabled = busy || !view.canReset;
    els.preamp.textContent = ok ? E.formatDb(s.preamp) : "— dB";
    els.preamp.title = ok && s.enabled === false && s.safetyPreamp
      ? `À la réactivation : ${E.formatDb(s.safetyPreamp)}` : "Préampli de sécurité appliqué";

    renderPresets(busy);
    els.confirm.hidden = !pendingConfirm;

    els.pill.className = `pill ${view.pill}`;
    els.pill.textContent = view.label;
    els.message.textContent = [...new Set(view.lines)].join("\n");
    els.message.classList.toggle("info", !view.alert);
  }

  async function action(path, body) {
    const opener = document.activeElement;
    actionBusy = true;
    pendingConfirm = null;
    render();
    try {
      await sync.idle();             // queued fader moves first, in order
      lastError = "";
      apply(await api("POST", path, body));
    } catch (err) {
      if (err.status === 409 && err.detail && err.detail.code === "confirm") {
        pendingConfirm = { path, opener, body: { ...(body || {}), confirm: true, revision: err.detail.revision } };
        els.confirmText.textContent = E.confirmText(err.detail);
        actionBusy = false;
        render();
        els.confirmCancel.focus();   // the safe choice has the focus
        return;
      }
      lastError = `Échec : ${err.message || err}`;
      await refresh();
    } finally {
      actionBusy = false;
      render();
    }
  }

  async function refresh() {
    clearTimeout(pollTimer);
    try {
      apply(await api("GET", "/api/eq"));
    } catch (err) {
      apply({ status: "server", message: `Serveur injoignable : ${err.message || err}` });
    }
    if (!document.hidden) pollTimer = setTimeout(poll, POLL_MS);
  }

  // Periodic re-read picks up changes made elsewhere (another tab, the file).
  function poll() {
    const held = Object.values(bands).some((b) => b.dragging);
    if (sync.busy() || actionBusy || held) { pollTimer = setTimeout(poll, POLL_MS); return; }
    refresh();
  }

  build();
  els.power.addEventListener("click", () => {
    action(status && status.enabled ? "/api/eq/disable" : "/api/eq/enable");
  });
  els.reset.addEventListener("click", () => action("/api/eq/reset"));
  // the confirmation hides itself: focus goes back to the button that opened it
  const back = (opener) => { if (opener && opener.isConnected && !opener.disabled) opener.focus(); };
  els.confirmOk.addEventListener("click", () => {
    const next = pendingConfirm;
    if (next) action(next.path, next.body).then(() => back(next.opener));
  });
  const cancel = () => {
    const opener = pendingConfirm && pendingConfirm.opener;
    pendingConfirm = null;
    render();
    back(opener);
  };
  els.confirmCancel.addEventListener("click", cancel);
  els.confirm.addEventListener("keydown", (e) => { if (e.key === "Escape") cancel(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  render();
  refresh();
})();
