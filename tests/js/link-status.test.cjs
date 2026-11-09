// node --test tests/js/link-status.test.cjs  (built-in runner, no dependency)
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const L = require("../../devialet_web/static/link-status.js");

const run = (events, start = "connecting") => events.reduce(
  (acc, e) => [...acc, L.nextLink(acc.at(-1) ?? start, e)], []);
const UP = { serverOk: true, online: true };
const DOWN = { serverOk: true, online: false };
const NO_SERVER = { serverOk: false, online: false };

test("connected while the leader answers", () => {
  assert.deepEqual(run([UP, UP]), ["connected", "connected"]);
  assert.equal(L.describe("connected", { leader: "192.0.2.11" }).label, "Connecté");
  assert.equal(L.describe("connected", { leader: "192.0.2.11" }).detail, "Leader 192.0.2.11");
});

test("network cut: Connexion perdue, then Reconnexion en cours, then Connecté", () => {
  const seq = run([UP, DOWN, DOWN, DOWN, UP]);
  assert.deepEqual(seq, ["connected", "lost", "reconnecting", "reconnecting", "connected"]);
  assert.equal(L.describe("lost").label, "Connexion perdue");
  assert.equal(L.describe("reconnecting").label, "Reconnexion en cours…");
  assert.match(L.describe("lost").detail, /Bluetooth et l’analyseur continuent/);
});

test("backend unreachable is reported the same way", () => {
  assert.deepEqual(run([UP, NO_SERVER, NO_SERVER, UP]), ["connected", "lost", "reconnecting", "connected"]);
  assert.deepEqual(run([NO_SERVER]), ["server"]);
  assert.equal(L.describe("server").label, "Serveur injoignable");
});

test("never connected since opening: speakers unreachable, not 'lost'", () => {
  assert.deepEqual(run([DOWN, DOWN]), ["offline", "offline"]);
});

test("tones: ok when connected, bad when lost, waiting while reconnecting", () => {
  assert.equal(L.describe("connected").tone, "ok");
  assert.equal(L.describe("lost").tone, "bad");
  assert.equal(L.describe("reconnecting").tone, "wait");
  assert.match(L.describe("connected", { leader: "x", leaderDetected: false }).detail, /non identifié/);
  assert.equal(L.describe("bogus").label, "Connexion…");
});
