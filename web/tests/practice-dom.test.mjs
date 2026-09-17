// Loads practice-ui.mjs against a minimal DOM for every hash route and fails when the page's
// own error handler caught anything (a missing function, a bad selector, a crashed render).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const moduleUrl = pathToFileURL(new URL("../practice-ui.mjs", import.meta.url).pathname).href;
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);

class Element {
  constructor(id = "", tagName = "DIV") {
    this.id = id; this.tagName = tagName; this.children = []; this.listeners = {}; this.value = ""; this.hidden = false;
    this.disabled = false; this.textContent = ""; this.className = ""; this.dataset = {}; this.attributes = {}; this.options = []; this.style = {};
    this.classList = { values: new Set(), add: (...v) => v.forEach(x => this.classList.values.add(x)), remove: (...v) => v.forEach(x => this.classList.values.delete(x)), toggle: (v, force) => { const on = force ?? !this.classList.values.has(v); this.classList.values[on ? "add" : "delete"](v); return on; }, contains: v => this.classList.values.has(v) };
  }
  addEventListener(type, handler) { (this.listeners[type] ??= []).push(handler); }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.children.push(item); return item; }
  replaceChildren(...items) { this.children = items; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  reset() {}
  reportValidity() { return true; }
  focus() {}
  scrollIntoView() {}
  scrollBy() {}
}

function installDom(hash) {
  const nodes = new Map(ids.map(id => [id, new Element(id, id.startsWith("draft-") || id === "target" ? "SELECT" : "DIV")]));
  const radios = { source: { value: "self", checked: true }, mode: { value: "training", checked: true } };
  globalThis.document = {
    body: new Element("body"),
    getElementById: id => nodes.get(id) ?? null,
    querySelector: selector => (selector.includes("[name=source]") ? radios.source : selector.includes("[name=mode]") ? radios.mode : null),
    querySelectorAll: () => [],
    createElement: tag => new Element("", tag.toUpperCase()),
    createTextNode: text => ({ textContent: text }),
  };
  globalThis.window = globalThis;
  globalThis.location = { hash, search: "", href: "http://test/", protocol: "http:", host: "test" };
  globalThis.history = { replaceState() {} };
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.scrollTo = () => {};
  globalThis.requestAnimationFrame = fn => fn();
  globalThis.addEventListener = () => {};
  return nodes;
}

const profile = {
  id: "alice", goal: { target_cefr: "C1", target_exam: "FSP" },
  details: { declared_level: "B1", level_source: "self", minutes_per_day: 30, land: "Bayern", estimated_level: null, maintenance_cadence_days: 30 },
};
const program = {
  model: { level: { reference: "B1" }, exam: { weeks_left: null } },
  week: {
    phase: "fondations", message: "m", limitations: "l", week_start: "2026-09-14", today: "2026-09-17", planned_minutes: 60, budget_minutes: 210,
    slots: [{ id: "lexicon_review-3", day_offset: 3, kind: "lexicon_review", minutes: 8, state: "today", rationale: "r", recommended: null }],
    next: { id: "lexicon_review-3", kind: "lexicon_review", rationale: "r", recommended: null, minutes: 8, state: "today" },
  },
};
const lexicon = {
  srs_version: "srs-sm2-v1", due_count: 1, maintenance_due_count: 0, active_count: 1, acquired_count: 0, maintenance_cadence_days: 30,
  by_state: { identified: 1, reviewed: 0, used: 0, mastered: 0 }, limitations: "l",
  entries: [{ id: "e1", lemma: "der Schmerz", translation: "la douleur", example: "", source: "manual", state: "identified", due: true, due_at: "2026-09-17T00:00:00Z", repetitions: 0, lapses: 0, used_sessions: 0, created_at: "2026-09-16T00:00:00Z", last_reviewed_at: null, zone: "active" }],
};
const progression = {
  state: "no_data", groups: [], excluded: [], limitations: "l",
  axes: {
    version: "progress-axes-v1", sessions_completed: 1, limitations: "axes",
    structure: { sessions: 1, columns: [{ id: "noxen", label: "Noxen" }], rows: [{ session_id: "s", date: "2026-09-17T00:00:00Z", mode: "training", cells: { noxen: 0.5 }, order_respected: true }], averages: { noxen: 0.5 }, weakest: [{ id: "noxen", label: "Noxen", ratio: 0.5 }], order_respected_rate: 1 },
    communication: { timeline: [{ session_id: "s", date: "2026-09-17T00:00:00Z", cue: "deuil", verdict: "ignored" }], counts: { ignored: 1 }, acknowledged_rate: 0, previous_rate: null },
    language: { sessions_considered: 1, by_category: { case: 2 }, previous_by_category: {}, recurring: [{ category: "case", count: 2, examples: [{ session_id: "s", text: "ex", turns: [1] }] }], errors_per_session: 2, previous_errors_per_session: null, code_switches: 0 },
    lexicon: { active: 1, acquired: 0, acquired_last_30_days: 0, added_last_30_days: 1, due: 1, maintenance_due: 0, by_state: {} },
    regularity: { weeks: [{ start: "2026-08-20", end: "2026-08-27", voice_sessions: 0, practice_runs: 0, voice_minutes: 0 }, { start: "2026-09-10", end: "2026-09-17", voice_sessions: 1, practice_runs: 0, voice_minutes: 12 }] },
    level: { history: [{ attempt_id: "a", date: "2026-09-16T00:00:00Z", band: "B1", vocab_grammar: "B1", listening: "B1", speaking: "A2", speaking_counted: false }], state: "estimated" },
  },
};
const placement = { available: true, set: { description_fr: "d" }, limitations: "l", latest: null, declared_level: "B1" };
const responses = {
  "/api/profile": profile, "/api/program": program, "/api/exercises": { items: [] }, "/api/cases?approved_only=true": [],
  "/api/history": { items: [] }, "/api/lexicon": lexicon, "/api/progression": progression, "/api/placement": placement,
};

async function load(hash) {
  const nodes = installDom(hash);
  const calls = [];
  globalThis.fetch = async path => {
    calls.push(String(path));
    const body = responses[String(path)];
    return { ok: body !== undefined, status: body === undefined ? 404 : 200, json: async () => body };
  };
  await import(`${moduleUrl}?dom-test=${encodeURIComponent(hash)}-${Math.random()}`);
  await new Promise(resolve => setTimeout(resolve, 20));
  return { nodes, calls };
}

for (const hash of ["#home", "#vocab", "#progress", "#placement", "#cases", "#history"]) {
  const { nodes } = await load(hash);
  assert.equal(nodes.get("error").hidden, true, `${hash}: ${nodes.get("error-text").textContent}`);
  const section = nodes.get(hash.slice(1));
  assert.equal(section.hidden, false, `${hash} view is shown`);
}

{
  const { nodes } = await load("#vocab");
  assert.equal(nodes.get("vocab-review").hidden, false, "a due word opens the review card");
  assert.match(nodes.get("review-front").textContent, /la douleur/);
  assert.match(nodes.get("tab-active").textContent, /À travailler \(1\)/);
}
{
  const { nodes } = await load("#progress");
  assert.equal(nodes.get("progress-tiles").children.length, 6);
  assert.equal(nodes.get("heatmap").children.length, 2, "header row plus one session row");
  assert.match(nodes.get("axis-structure-note").textContent, /Noxen/);
}
{
  const { nodes } = await load("#home");
  assert.match(nodes.get("recommendation").children[1].textContent, /Réviser mon carnet/);
  assert.equal(nodes.get("week-days").children.length, 7);
}
console.log("Practice DOM: every route renders without a caught error; Carnet, Progression and home show their data");
