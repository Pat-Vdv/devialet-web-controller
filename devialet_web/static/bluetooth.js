"use strict";

// Windows audio output card (Bluetooth). Independent from app.js.
(() => {
  const SLOW_MS = 4000;
  const FAST_MS = 1000; // while the pair is discoverable
  const $ = (id) => document.getElementById(id);
  const els = {
    pill: $("bt-pill"), status: $("bt-status"), stream: $("bt-stream"),
    wizard: $("bt-wizard"), stepFree: $("bt-step-free"), pair: $("btn-pair"),
    countdown: $("bt-countdown"), name: $("bt-name"), name2: $("bt-name2"),
    message: $("bt-message"),
  };

  let timer = null;
  let peerAtPairing; // undefined: no pairing in progress from this page
  let last = null;

  function setMessage(text, kind = "") {
    els.message.textContent = text;
    els.message.className = `small ${kind}`;
  }

  function streamText(s) {
    if (!s) return "";
    const parts = [];
    if (s.codec) parts.push(s.codec.toUpperCase());
    if (s.bitDepth && s.samplingRate) parts.push(`${s.bitDepth} bits / ${(s.samplingRate / 1000).toLocaleString("fr-FR")} kHz`);
    if (s.channels) parts.push(s.channels);
    if (s.lossless !== undefined) parts.push(s.lossless ? "sans perte" : "avec perte");
    return parts.join(" · ");
  }

  function render(bt) {
    last = bt;
    const name = `« ${bt.systemName || "My Phantom System"} »`;
    els.name.textContent = name;
    els.name2.textContent = name;

    if (!bt.available) {
      els.pill.className = "pill pill-bad";
      els.pill.textContent = "Hors ligne";
      els.status.textContent = "Enceintes injoignables";
      els.stream.textContent = "";
    } else if (bt.peerDeviceName) {
      els.pill.className = "pill pill-ok";
      els.pill.textContent = "Connecté";
      els.status.textContent = bt.peerDeviceName;
      els.stream.textContent = streamText(bt.stream);
    } else {
      els.pill.className = "pill pill-wait";
      els.pill.textContent = bt.sourceActive ? "Libre" : "Source inactive";
      els.status.textContent = bt.sourceActive ? "Aucun appareil connecté"
        : "La source active n'est pas le Bluetooth";
      els.stream.textContent = "";
    }

    els.stepFree.textContent = bt.peerDeviceName
      ? `Libérez l'enceinte : coupez le Bluetooth de « ${bt.peerDeviceName} » (ou déconnectez-le).`
      : "L'enceinte est libre : aucun appareil Bluetooth connecté.";

    if (bt.advertising) {
      const windowS = bt.pairingWindowS || 120;
      const left = bt.pairingStartedAt
        ? Math.max(0, Math.round(bt.pairingStartedAt + windowS - Date.now() / 1000)) : null;
      els.countdown.textContent = left === null ? "Visible maintenant"
        : `Visible encore ≈ ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
      els.pair.disabled = true;
    } else {
      els.countdown.textContent = bt.advertising === false && peerAtPairing !== undefined
        ? "Mode appairage terminé" : "";
      els.pair.disabled = !bt.available;
    }

    // Success: a (new) device connected after we opened the pairing window.
    if (peerAtPairing !== undefined && bt.peerDeviceName && bt.peerDeviceName !== peerAtPairing) {
      setMessage(`✓ ${bt.peerDeviceName} est connecté. Choisissez maintenant ${name} dans la sortie audio Windows.`, "ok");
      peerAtPairing = undefined;
    } else if (peerAtPairing !== undefined && bt.advertising === false && !bt.peerDeviceName) {
      setMessage("Le délai d'appairage est écoulé sans connexion. Relancez l'étape 3 juste après avoir ouvert la recherche Windows.", "bad");
      peerAtPairing = undefined;
    }
    if (bt.errors?.length) setMessage(bt.errors.join(" · "), "bad");
  }

  async function refresh() {
    clearTimeout(timer);
    try {
      const res = await fetch("/api/bluetooth");
      render(await res.json());
    } catch (err) {
      els.pill.className = "pill pill-bad";
      els.pill.textContent = "Serveur injoignable";
    }
    if (!document.hidden) timer = setTimeout(refresh, last?.advertising ? FAST_MS : SLOW_MS);
  }

  els.pair.addEventListener("click", async () => {
    els.pair.disabled = true;
    setMessage("");
    try {
      const res = await fetch("/api/bluetooth/pairing", { method: "POST", headers: { "X-Devialet-Request": "1" } });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : data.detail?.code || `HTTP ${res.status}`);
      peerAtPairing = data.peerDeviceName || null;
      setMessage("L'enceinte est visible : sélectionnez-la dans la fenêtre Windows « Ajouter un appareil ».");
      render(data);
    } catch (err) {
      setMessage(`Échec : ${err.message || err}`, "bad");
      els.pair.disabled = false;
    }
    refresh();
  });

  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  refresh();
})();
