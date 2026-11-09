"use strict";

// Connection indicator (V2.4.2), pure so it can be tested with node.
// From two facts per poll (backend answered? leader reachable?) and the
// previous state, it tells the user: Connecté / Connexion perdue /
// Reconnexion en cours. The network strategy itself is unchanged: app.js
// keeps polling and the backend re-probes the speakers on its own.
(function (root) {
  const STATES = Object.freeze({
    connecting: { label: "Connexion…", tone: "wait" },
    connected: { label: "Connecté", tone: "ok" },
    lost: { label: "Connexion perdue", tone: "bad" },
    reconnecting: { label: "Reconnexion en cours…", tone: "wait" },
    offline: { label: "Enceintes injoignables", tone: "bad" },
    server: { label: "Serveur injoignable", tone: "bad" },
  });

  function nextLink(prev, { serverOk, online }) {
    const was = prev && STATES[prev] ? prev : "connecting";
    if (!serverOk) return was === "connected" ? "lost" : (was === "connecting" ? "server" : "reconnecting");
    if (online) return "connected";
    if (was === "connected") return "lost";
    if (was === "lost" || was === "reconnecting" || was === "server") return "reconnecting";
    return "offline";   // never connected since the page opened
  }

  function describe(key, { leader = null, leaderDetected = true } = {}) {
    const state = STATES[key] || STATES.connecting;
    let detail = "";
    if (key === "connected") detail = leaderDetected ? `Leader ${leader}` : `Leader non identifié (${leader})`;
    if (key === "lost" || key === "reconnecting") detail = "commandes indisponibles · le son Bluetooth et l’analyseur continuent";
    return { key, label: state.label, tone: state.tone, detail };
  }

  const api = { STATES, nextLink, describe };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.LinkStatus = api;
})(typeof window !== "undefined" ? window : globalThis);
