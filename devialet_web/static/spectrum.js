"use strict";

// Spectrum analyzer panel (V2.3 / V2.3.1). Real data only: levels come from
// the Windows agent through /api/spectrum/ws; when they are not available the
// screen stays dark with an explicit message (no simulated animation).
// Themes, sensitivity and peak hold change the drawing only, never the data.
// V2.4.2: the palette follows the global theme (theme.js), the only source of truth.
(() => {
  const B = window.SpectrumBallistics;
  const S = window.SpectrumSettings;
  const G = window.DevialetTheme;
  const LABELS = ["31", "62", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"];
  const SEGMENTS = 30;            // 2 dB per segment on a 60 dB scale
  const DATA_TIMEOUT_MS = 1500;   // the agent sends >= 2 frames/s, even in silence
  const TRACK_POLL_MS = 3000;     // title/artist refresh while in fullscreen

  const $ = (id) => document.getElementById(id);
  const els = {
    card: $("spectrum-card"), screen: $("spectrum-screen"), canvas: $("spectrum-canvas"),
    overlay: $("spectrum-overlay"), pill: $("spectrum-pill"), toggle: $("spectrum-toggle"),
    peak: $("spectrum-peak"), hold: $("spectrum-hold"), stereo: $("spectrum-stereo"),
    mono: $("spectrum-mono"), sensitivity: $("spectrum-sensitivity"),
    sensitivityValue: $("spectrum-sensitivity-value"), fullscreen: $("spectrum-fullscreen"),
    exit: $("spectrum-exit"), track: $("spectrum-track"), info: $("spectrum-info"),
    controls: $("spectrum-controls"), themeControl: $("theme-control"), themeHome: $("theme-home"),
  };
  const ctx = els.canvas.getContext("2d");
  const storage = (() => { try { return window.localStorage; } catch (e) { return null; } })();

  let prefs = S.load(storage);
  const opts = { peakHoldMs: prefs.peakHoldMs };
  const left = B.create(10, opts), right = B.create(10, opts), mono = B.create(10, opts);
  const fullscreen = S.createFullscreen(els.card, document);
  let palette = null;
  let status = null, lastFrame = null, fullscreenError = "";
  let socket = null, socketOpen = false, retryMs = 1000;
  let lastLevelsAt = 0, lastFrameAt = 0, rafId = 0, trackTimer = 0;
  let size = { w: 0, h: 0, dpr: 1 };

  function savePrefs() { S.save(storage, prefs); }

  // --- state ------------------------------------------------------------------
  const isMonoSource = () => Boolean(status && status.agent && status.agent.mono);
  const showMono = () => prefs.mode === "mono" || isMonoSource();
  const live = () => socketOpen && status && status.available;

  function clearBars() {
    B.reset(left); B.reset(right); B.reset(mono);
  }

  // Display targets from the last received frame + visual sensitivity.
  function applyFrame() {
    if (!lastFrame) return;
    const l = S.applySensitivity(lastFrame.l, prefs.sensitivity);
    const r = S.applySensitivity(lastFrame.r, prefs.sensitivity);
    B.setTargets(left, l);
    B.setTargets(right, r);
    // mono: power average of both channels
    B.setTargets(mono, l.map((v, i) => 10 * Math.log10((10 ** (v / 10) + 10 ** (r[i] / 10)) / 2)));
  }

  function onLevels(msg) {
    if (!live()) return;
    lastLevelsAt = performance.now();
    lastFrame = { l: msg.l, r: msg.r };
    applyFrame();
    startAnimation();
  }

  function onStatus(msg) {
    const wasLive = live();
    status = msg;
    if (prefs.enabled && !msg.enabled && !onStatus.restored) {
      onStatus.restored = true;  // restore the user's choice once after a server restart
      setEnabled(true);
    }
    onStatus.restored = true;
    if (wasLive && !live()) { clearBars(); lastFrame = null; }
    render();
  }

  // --- settings -----------------------------------------------------------------
  function applyTheme() {
    els.card.dataset.theme = G.spectrumPalette(G.currentTheme(document));
    const css = getComputedStyle(els.card);
    palette = S.readPalette((name) => css.getPropertyValue(name));
    draw();
  }

  function syncControls() {
    els.sensitivity.value = String(prefs.sensitivity);
    els.sensitivityValue.textContent = S.formatSensitivity(prefs.sensitivity);
    els.peak.checked = prefs.peakHold;
    els.hold.value = String(prefs.peakHoldMs);
    els.hold.disabled = !prefs.peakHold;
  }

  document.addEventListener(G.EVENT, applyTheme);
  els.sensitivity.addEventListener("input", () => {
    prefs = S.sanitize(Object.assign(prefs, { sensitivity: Number(els.sensitivity.value) }));
    savePrefs(); syncControls(); applyFrame(); startAnimation();
  });
  els.peak.addEventListener("change", () => {
    prefs.peakHold = els.peak.checked; savePrefs(); syncControls();
  });
  els.hold.addEventListener("change", () => {
    prefs = S.sanitize(Object.assign(prefs, { peakHoldMs: Number(els.hold.value) }));
    for (const s of [left, right, mono]) B.configure(s, { peakHoldMs: prefs.peakHoldMs });
    savePrefs(); syncControls();
  });
  els.stereo.addEventListener("click", () => { prefs.mode = "stereo"; savePrefs(); render(); });
  els.mono.addEventListener("click", () => { prefs.mode = "mono"; savePrefs(); render(); });
  els.toggle.addEventListener("click", () => setEnabled(!(status && status.enabled)));

  // --- fullscreen ---------------------------------------------------------------
  async function toggleFullscreen() {
    const result = await fullscreen.toggle();
    fullscreenError = result.ok ? "" : result.error;
    render();
  }

  async function refreshTrack() {
    if (!fullscreen.active()) return;
    try {
      const state = await (await fetch("/api/state")).json();
      const src = state && state.source;
      els.track.textContent = src && src.present && (src.title || src.artist)
        ? [src.title, src.artist].filter(Boolean).join(" — ") : "";
    } catch (e) {
      els.track.textContent = "";
    }
  }

  function onFullscreenChange() {
    const active = fullscreen.active();
    els.fullscreen.textContent = active ? "Quitter le plein écran" : "Plein écran";
    G.dock(els.themeControl, els.themeHome, els.controls, active);  // one selector, moved
    clearInterval(trackTimer);
    if (active) {
      refreshTrack();
      trackTimer = setInterval(refreshTrack, TRACK_POLL_MS);
    } else {
      els.track.textContent = "";  // the title is only shown in fullscreen (Esc lands here too)
    }
    resize();
  }

  els.fullscreen.addEventListener("click", toggleFullscreen);
  els.exit.addEventListener("click", toggleFullscreen);
  document.addEventListener("fullscreenchange", onFullscreenChange);
  document.addEventListener("webkitfullscreenchange", onFullscreenChange);
  document.addEventListener("fullscreenerror", () => {
    fullscreenError = "Plein écran refusé par le navigateur";
    render();
  });

  // --- UI -----------------------------------------------------------------------
  function render() {
    const enabled = Boolean(status && status.enabled);
    els.toggle.textContent = enabled ? "Désactiver" : "Activer";
    els.toggle.disabled = !socketOpen;
    els.stereo.classList.toggle("on", !showMono());
    els.mono.classList.toggle("on", showMono());
    els.stereo.setAttribute("aria-pressed", String(!showMono()));
    els.mono.setAttribute("aria-pressed", String(showMono()));
    els.stereo.disabled = isMonoSource();
    els.fullscreen.disabled = !fullscreen.supported();

    const agent = status && status.agent;
    if (!socketOpen) setPill("bad", "Serveur injoignable");
    else if (!agent || !agent.connected) setPill("bad", "Agent absent");
    else if (agent.stale) setPill("bad", "Agent figé");
    else setPill(status.available ? "ok" : "wait", "Agent connecté");

    let overlay = "";
    if (!socketOpen) overlay = "Analyse audio indisponible\nServeur injoignable";
    else if (!enabled) overlay = "Analyseur désactivé";
    else if (!status.available) overlay = "Analyse audio indisponible\n" + status.reason;
    els.overlay.textContent = overlay;

    const info = [];
    if (fullscreenError) info.push(fullscreenError);
    if (agent && agent.connected) {
      if (agent.device) info.push(agent.device);
      if (agent.format) info.push(agent.format);
      if (agent.isDefault === false) info.push("⚠ ce n'est pas la sortie Windows par défaut");
      if (isMonoSource()) info.push("source mono : gauche et droite identiques");
    } else if (socketOpen) {
      info.push("Démarrez DevialetSpectrumAgent.exe sous Windows.");
    }
    els.info.textContent = info.join(" · ");
    if (!live()) draw();  // one static frame, no animation
  }

  function setPill(kind, text) {
    els.pill.className = `pill pill-${kind}`;
    els.pill.textContent = text;
  }

  async function setEnabled(enabled) {
    prefs.enabled = enabled;
    savePrefs();
    try {
      const res = await fetch("/api/spectrum", {
        method: "POST", headers: { "Content-Type": "application/json", "X-Devialet-Request": "1" },
        body: JSON.stringify({ enabled }),
      });
      if (res.ok) onStatus(await res.json());
    } catch (e) { /* the WebSocket status will tell */ }
  }

  // --- WebSocket ------------------------------------------------------------------
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${proto}://${location.host}/api/spectrum/ws`);
    socket.onopen = () => { socketOpen = true; retryMs = 1000; render(); };
    socket.onmessage = (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch (e) { return; }
      if (msg.type === "levels") onLevels(msg);
      else if (msg.type === "status") onStatus(msg);
    };
    socket.onclose = () => {
      socketOpen = false;
      clearBars();
      lastFrame = null;
      render();
      setTimeout(connect, retryMs);
      retryMs = Math.min(retryMs * 2, 5000);
    };
  }

  // --- drawing --------------------------------------------------------------------
  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const w = els.screen.clientWidth, h = els.screen.clientHeight;
    if (w === size.w && h === size.h && dpr === size.dpr) return;
    size = { w, h, dpr };
    // backing store = CSS size x dpr: never stretched, so bars are not distorted
    els.canvas.width = Math.round(w * dpr);
    els.canvas.height = Math.round(h * dpr);
    draw();
  }

  function drawGroup(state, x, y, w, h, title, peaks, fontPx) {
    const slot = w / 10, barW = Math.max(3, slot * 0.66), segGap = Math.max(1, h * 0.006);
    const segH = (h - segGap * (SEGMENTS - 1)) / SEGMENTS;
    ctx.font = `${fontPx}px ui-monospace, Consolas, monospace`;
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (let i = 0; i < 10; i++) {
      const bx = x + i * slot + (slot - barW) / 2;
      const lit = Math.round(state.level[i] * SEGMENTS);
      for (let s = 0; s < SEGMENTS; s++) {
        const sy = y + h - (s + 1) * segH - s * segGap;
        ctx.fillStyle = s < lit ? S.mixColor(palette.low, palette.high, s / (SEGMENTS - 1)) : palette.off;
        ctx.fillRect(bx, sy, barW, segH);
      }
      if (peaks) {
        const p = Math.min(SEGMENTS - 1, Math.round(state.peak[i] * SEGMENTS) - 1);
        if (p >= 0 && state.peak[i] > 0.02) {
          const py = y + h - (p + 1) * segH - p * segGap;
          ctx.save();
          ctx.shadowColor = palette.peakGlow;
          ctx.shadowBlur = 8 * size.dpr;
          ctx.fillStyle = palette.peak;
          ctx.fillRect(bx, py, barW, segH);
          ctx.restore();
        }
      }
      ctx.fillStyle = palette.label;
      ctx.fillText(LABELS[i], x + i * slot + slot / 2, y + h + fontPx * 0.5);
    }
    ctx.textAlign = "left";
    ctx.textBaseline = "bottom";
    ctx.fillStyle = palette.accent;
    ctx.font = `600 ${Math.round(fontPx * 1.15)}px ui-monospace, Consolas, monospace`;
    ctx.fillText(title, x, y - fontPx * 0.4);
  }

  function draw() {
    const W = els.canvas.width, H = els.canvas.height, dpr = size.dpr;
    if (!W || !H || !palette) return;
    ctx.clearRect(0, 0, W, H);
    // text scales with the screen (bigger in fullscreen), within readable bounds
    const fontPx = Math.round(Math.min(18 * dpr, Math.max(10 * dpr, H * 0.035)));
    const padL = fontPx * 4, padR = 12 * dpr, padT = fontPx * 2.2, padB = fontPx * 2.2;
    const plotH = H - padT - padB;

    // dB scale and grid
    ctx.font = `${fontPx}px ui-monospace, Consolas, monospace`;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let db = 0; db >= -60; db -= 10) {
      const yy = padT + plotH * (-db / 60);
      ctx.fillStyle = palette.label;
      ctx.fillText(db === 0 ? "0 dB" : String(db), padL - 8 * dpr, yy);
      ctx.fillStyle = palette.grid;
      ctx.fillRect(padL, Math.round(yy), W - padL - padR, Math.max(1, dpr));
    }

    const peaks = prefs.peakHold;
    const plotW = W - padL - padR;
    if (showMono()) {
      drawGroup(mono, padL, padT, plotW, plotH, "L+R", peaks, fontPx);
    } else {
      const gap = 28 * dpr, groupW = (plotW - gap) / 2;
      drawGroup(left, padL, padT, groupW, plotH, "L · GAUCHE", peaks, fontPx);
      drawGroup(right, padL + groupW + gap, padT, groupW, plotH, "R · DROITE", peaks, fontPx);
      ctx.fillStyle = palette.grid;
      ctx.fillRect(padL + groupW + gap / 2, padT, Math.max(1, dpr), plotH);
    }
  }

  function frame(now) {
    rafId = 0;
    if (document.hidden || !live()) return;
    if (now - lastLevelsAt > DATA_TIMEOUT_MS) {
      // stalled data: let the bars fall (real measurement of "nothing received")
      lastFrame = null;
      B.setTargets(left, []); B.setTargets(right, []); B.setTargets(mono, []);
    }
    const dt = lastFrameAt ? now - lastFrameAt : 16;
    lastFrameAt = now;
    const hold = prefs.peakHold;
    B.step(left, dt, now, hold); B.step(right, dt, now, hold); B.step(mono, dt, now, hold);
    draw();
    rafId = requestAnimationFrame(frame);
  }

  function startAnimation() {
    if (!rafId && !document.hidden && live()) {
      lastFrameAt = 0;
      rafId = requestAnimationFrame(frame);
    }
  }

  document.addEventListener("visibilitychange", () => { if (!document.hidden) startAnimation(); });
  if (window.ResizeObserver) new ResizeObserver(resize).observe(els.screen);
  window.addEventListener("resize", resize);
  syncControls();
  applyTheme();
  resize();
  render();
  connect();
})();
