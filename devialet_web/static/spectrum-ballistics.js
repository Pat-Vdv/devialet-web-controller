"use strict";

// Bar and peak ballistics of the spectrum analyzer (pure, no DOM).
// Levels are normalised: 0 = minDb (bottom of the scale), 1 = maxDb (0 dBFS).
//   - bars rise instantly to the measured level and fall at `releasePerSec`;
//   - peaks stay `peakHoldMs` above the bar, then fall with constant
//     acceleration (`peakGravity`), never below the bar.
(function (root) {
  const DEFAULTS = Object.freeze({
    minDb: -60,
    maxDb: 0,
    releasePerSec: 0.55,  // ~33 dB/s on a 60 dB scale
    peakHoldMs: 800,
    peakGravity: 2.5,     // normalised units / s^2
    maxStepMs: 100,       // a hidden tab must not make bars jump
  });

  function dbToLevel(db, opts) {
    const o = opts || DEFAULTS;
    if (typeof db !== "number" || !Number.isFinite(db)) return 0;
    return Math.min(1, Math.max(0, (db - o.minDb) / (o.maxDb - o.minDb)));
  }

  function create(count, opts) {
    return {
      opts: Object.assign({}, DEFAULTS, opts || {}),
      target: new Float64Array(count),
      level: new Float64Array(count),
      peak: new Float64Array(count),
      peakAt: new Float64Array(count),
      peakVelocity: new Float64Array(count),
    };
  }

  // Change options on a live state (e.g. peakHoldMs); step() reads them every
  // frame, so the new value applies immediately, including to a peak being held.
  function configure(state, opts) {
    Object.assign(state.opts, opts || {});
    return state;
  }

  function setTargets(state, dbValues) {
    for (let i = 0; i < state.target.length; i++) {
      state.target[i] = dbToLevel(dbValues[i], state.opts);
    }
  }

  function reset(state) {
    for (const key of ["target", "level", "peak", "peakAt", "peakVelocity"]) state[key].fill(0);
  }

  function step(state, dtMs, nowMs, peakHold) {
    const o = state.opts;
    const dt = Math.min(Math.max(dtMs, 0), o.maxStepMs) / 1000;
    for (let i = 0; i < state.level.length; i++) {
      const target = state.target[i];
      let level = state.level[i];
      level = target >= level ? target : Math.max(target, level - o.releasePerSec * dt);
      state.level[i] = level;

      if (peakHold === false) {
        state.peak[i] = level;
        state.peakVelocity[i] = 0;
        state.peakAt[i] = nowMs;
        continue;
      }
      if (level >= state.peak[i]) {
        state.peak[i] = level;
        state.peakAt[i] = nowMs;
        state.peakVelocity[i] = 0;
      } else if (nowMs - state.peakAt[i] >= o.peakHoldMs) {
        state.peakVelocity[i] += o.peakGravity * dt;
        state.peak[i] = Math.max(level, state.peak[i] - state.peakVelocity[i] * dt);
      }
    }
    return state;
  }

  const api = { DEFAULTS, dbToLevel, create, configure, setTargets, reset, step };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SpectrumBallistics = api;
})(typeof window !== "undefined" ? window : globalThis);
