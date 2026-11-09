"use strict";

const POLL_MS = 2000;
const SLIDER_THROTTLE_MS = 250;
const HOLD_AFTER_INPUT_MS = 1500; // ignore polled volume right after a user change

const $ = (id) => document.getElementById(id);
const els = {
  system: $("system-name"), link: $("link"), linkLabel: $("link-label"),
  linkDetail: $("link-detail"), sysSummary: $("sysinfo-summary"), speakers: $("speakers"),
  source: $("source-name"), title: $("track-title"), artist: $("track-artist"),
  playState: $("play-state"), toggle: $("btn-toggle"), prev: $("btn-previous"),
  next: $("btn-next"), iconPlay: $("icon-play"), iconPause: $("icon-pause"),
  slider: $("volume"), volValue: $("volume-value"), volDown: $("btn-vol-down"),
  volUp: $("btn-vol-up"), volNote: $("volume-note"), errors: $("errors"),
};

let state = null;
let dragging = false;
let holdUntil = 0;
let pollTimer = null;
let actionError = "";
let link = "connecting";   // LinkStatus key, see link-status.js

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    // X-Devialet-Request: required by the server on writes (cross-site protection, security.py)
    headers: body ? { "Content-Type": "application/json", "X-Devialet-Request": "1" } : { "X-Devialet-Request": "1" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error(typeof d === "string" ? d : d?.message || d?.code || `HTTP ${res.status}`);
  }
  return data;
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function renderSpeakers(speakers) {
  const order = { FrontLeft: 0, FrontRight: 1 };
  const sorted = [...speakers].sort((a, b) => (order[a.role] ?? 9) - (order[b.role] ?? 9));
  els.speakers.replaceChildren(...sorted.map((s) => {
    const card = el("div", `speaker ${s.reachable ? "up" : "down"}`);
    const role = el("div", "role");
    role.append(el("span", "dot"), el("span", null, s.reachable ? s.roleLabel : "Injoignable"));
    if (s.isLeader) role.append(el("span", "badge", "Leader"));
    card.append(role,
      el("span", "muted small", s.name || "—"),
      el("span", "muted small", s.host),
      el("span", "muted small", s.reachable ? `Firmware ${s.firmware || "?"}` : (s.error || "")));
    return card;
  }));
}

function renderSource(src) {
  if (!src || !src.present) {
    els.source.textContent = "Aucune source active";
    els.title.textContent = "—";
    els.artist.textContent = "";
    els.playState.textContent = "";
    return;
  }
  els.source.textContent = src.peerDeviceName ? `${src.name} · ${src.peerDeviceName}` : src.name;
  els.title.textContent = src.title || "Titre inconnu";
  els.artist.textContent = [src.artist, src.album].filter(Boolean).join(" — ");
  const labels = { playing: "Lecture", paused: "En pause" };
  els.playState.textContent = labels[src.playingState] || src.playingState || "";
}

function renderTransport(src, online) {
  const ops = new Set(src?.availableOperations || []);
  const known = ops.size > 0; // if the speaker lists nothing, do not block the buttons
  const allowed = (op) => online && src?.present && (!known || ops.has(op));
  const playing = src?.playingState === "playing";
  els.iconPlay.hidden = playing;
  els.iconPause.hidden = !playing;
  els.toggle.setAttribute("aria-label", playing ? "Pause" : "Lecture");
  els.toggle.disabled = !allowed(playing ? "pause" : "play");
  els.prev.disabled = !allowed("previous");
  els.next.disabled = !allowed("next");
}

function renderVolume(s) {
  const online = s.leader !== null;
  els.slider.max = String(s.maxVolume);
  const volume = s.volumePending ?? s.volume;
  const holding = dragging || Date.now() < holdUntil;
  if (!holding && volume !== null && volume !== undefined) {
    els.slider.value = String(Math.min(volume, s.maxVolume));
    els.volValue.textContent = String(volume);
  } else if (!holding) {
    els.volValue.textContent = "—";
  }
  const current = holding ? Number(els.slider.value) : volume;
  els.slider.disabled = !online || volume === null;
  els.volDown.disabled = !online || volume === null || current <= 0;
  els.volUp.disabled = !online || volume === null || current >= s.maxVolume;
  els.volNote.textContent = volume > s.maxVolume
    ? `Volume actuel au-dessus du plafond (${s.maxVolume}) : seule la baisse est possible.`
    : `Plafond : ${s.maxVolume}`;
}

function renderLink(s) {
  const d = LinkStatus.describe(link, { leader: s?.leader, leaderDetected: s?.leaderDetected });
  const tone = d.key === "connected" && !s?.leaderDetected ? "wait" : d.tone;
  els.link.className = `pill pill-${tone} link-pill link-${d.key}`;
  els.linkLabel.textContent = d.label;
  els.linkDetail.textContent = d.detail;
  document.documentElement.dataset.link = d.key;
  if (!s) return;
  const up = (s.speakers || []).filter((sp) => sp.reachable).length;
  els.sysSummary.textContent = s.speakers?.length
    ? `${up}/${s.speakers.length} enceintes joignables${s.leader ? ` · leader ${s.leader}` : ""}`
    : "";
}

function render() {
  if (!state) return;
  const online = state.leader !== null;
  els.system.textContent = state.system.name || "Phantom II";
  document.title = `${state.system.name || "Phantom II"} · Devialet`;
  renderLink(state);
  renderSpeakers(state.speakers);
  renderSource(state.source);
  renderTransport(state.source, online);
  renderVolume(state);
  const errors = [...(state.errors || [])];
  if (state.system.error) errors.push(`Système : ${state.system.error}`);
  if (actionError) errors.push(actionError);
  els.errors.textContent = errors.join("\n");
}

async function poll() {
  clearTimeout(pollTimer);
  try {
    state = await api("GET", "/api/state");
    link = LinkStatus.nextLink(link, { serverOk: true, online: state.leader !== null });
  } catch (err) {
    link = LinkStatus.nextLink(link, { serverOk: false, online: false });
    // Last known values are no longer confirmed: show them as unavailable.
    state = state && { ...state, leader: null, volume: null, volumePending: null,
      source: { present: false }, errors: [] };
    renderLink(state);
    render();
    els.errors.textContent = String(err.message || err);
    if (!document.hidden) pollTimer = setTimeout(poll, POLL_MS);
    return;
  }
  render();
  if (!document.hidden) pollTimer = setTimeout(poll, POLL_MS);
}

async function run(action) {
  actionError = "";
  try {
    await action();
  } catch (err) {
    actionError = String(err.message || err);
  }
  setTimeout(poll, 300);
}

// Volume slider: throttled while dragging, final value on release.
let lastSent = 0;
let trailing = null;
function sendVolume(value) {
  lastSent = Date.now();
  holdUntil = Date.now() + HOLD_AFTER_INPUT_MS;
  run(() => api("POST", "/api/volume", { volume: value }));
}
els.slider.addEventListener("input", () => {
  dragging = true;
  const value = Number(els.slider.value);
  els.volValue.textContent = String(value);
  clearTimeout(trailing);
  const wait = SLIDER_THROTTLE_MS - (Date.now() - lastSent);
  if (wait <= 0) sendVolume(value);
  else trailing = setTimeout(() => sendVolume(Number(els.slider.value)), wait);
});
els.slider.addEventListener("change", () => {
  dragging = false;
  clearTimeout(trailing);
  sendVolume(Number(els.slider.value));
});

function step(direction) {
  holdUntil = Date.now() + HOLD_AFTER_INPUT_MS;
  run(async () => {
    const { volume } = await api("POST", "/api/volume/step", { direction });
    els.slider.value = String(volume);
    els.volValue.textContent = String(volume);
  });
}
els.volDown.addEventListener("click", () => step(-1));
els.volUp.addEventListener("click", () => step(1));

els.toggle.addEventListener("click", () => {
  const playing = state?.source?.playingState === "playing";
  run(() => api("POST", `/api/playback/${playing ? "pause" : "play"}`));
});
els.prev.addEventListener("click", () => run(() => api("POST", "/api/playback/previous")));
els.next.addEventListener("click", () => run(() => api("POST", "/api/playback/next")));

document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
poll();
