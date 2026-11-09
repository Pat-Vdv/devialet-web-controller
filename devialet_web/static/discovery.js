"use strict";

// Discovery panel (V2.1). Independent from app.js: after a selection, the
// regular /api/state polling of app.js picks up the new system by itself.
(() => {
  const ROLE = { FrontLeft: "Gauche", FrontRight: "Droite", Mono: "Mono" };
  const TYPE = { stereo: "Stéréo", single: "Enceinte seule", multi: "Multi", surround: "Surround" };
  const scanBtn = document.getElementById("btn-scan");
  const statusEl = document.getElementById("scan-status");
  const listEl = document.getElementById("systems-list");

  async function call(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json", "X-Devialet-Request": "1" } : { "X-Devialet-Request": "1" },  // see security.py
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
    return data;
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function speakerLine(s) {
    const parts = [ROLE[s.role] || s.role || "?", s.name, s.host, s.model,
      s.firmware && `fw ${s.firmware}`, s.isLeader && "leader",
      !s.reachable && "injoignable"];
    return parts.filter(Boolean).join(" · ");
  }

  function render(data) {
    const systems = data.systems || [];
    listEl.replaceChildren(...systems.map((sys) => {
      const selected = sys.systemId === data.selectedSystemId;
      const item = el("li", `system${selected ? " selected" : ""}${sys.available ? "" : " down"}`);
      const top = el("div", "system-top");
      const name = el("span", "system-name");
      const dot = el("span", `dot ${sys.available ? (sys.complete ? "up" : "partial") : ""}`);
      dot.title = sys.available ? (sys.complete ? "Disponible" : "Partiellement disponible") : "Indisponible";
      name.append(dot, el("span", null, sys.name || "Système sans nom"),
        el("span", "muted small", TYPE[sys.type] || sys.type || ""));
      top.append(name);
      if (selected) {
        top.append(el("span", "badge", "Sélectionné"));
      } else {
        const btn = el("button", "text-btn", "Sélectionner");
        btn.disabled = !sys.available;
        btn.addEventListener("click", () => select(sys.systemId, btn));
        top.append(btn);
      }
      const speakers = el("ul", "system-speakers");
      speakers.append(...sys.speakers.map((s) => el("li", null, speakerLine(s))));
      for (const m of sys.missing || []) {
        speakers.append(el("li", null, `${ROLE[m.role] || m.role || "?"} · ${m.name || m.deviceId} · non trouvée`));
      }
      item.append(top, speakers);
      return item;
    }));
    if (data.running) {
      statusEl.textContent = "Recherche en cours…";
    } else if (data.startedAt) {
      const when = new Date(data.startedAt * 1000).toLocaleTimeString();
      const strategies = Object.entries(data.strategies || {})
        .map(([k, v]) => `${k} ${v.found}/${v.probed}`).join(", ");
      statusEl.textContent = `${systems.length} système(s) · ${data.probed} adresse(s) sondée(s) ` +
        `en ${data.durationS} s à ${when}` + (strategies ? ` · ${strategies}` : "") +
        (data.errors?.length ? `\n${data.errors.join("\n")}` : "");
    } else {
      statusEl.textContent = "Aucune recherche effectuée.";
    }
  }

  async function load() {
    try {
      render(await call("GET", "/api/discovery"));
    } catch (err) {
      statusEl.textContent = String(err.message || err);
    }
  }

  async function scan() {
    scanBtn.disabled = true;
    statusEl.textContent = "Recherche en cours…";
    try {
      render(await call("POST", "/api/discovery/scan"));
    } catch (err) {
      statusEl.textContent = String(err.message || err);
    } finally {
      scanBtn.disabled = false;
    }
  }

  async function select(systemId, btn) {
    btn.disabled = true;
    try {
      render(await call("POST", "/api/systems/select", { systemId }));
    } catch (err) {
      statusEl.textContent = String(err.message || err);
      btn.disabled = false;
    }
  }

  scanBtn.addEventListener("click", scan);
  load();
})();
