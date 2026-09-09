import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const appUrl = pathToFileURL(new URL("../app.js", import.meta.url).pathname).href;
const html = readFileSync(new URL("../voice.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);

class Element {
  constructor(id = "") { this.id = id; this.children = []; this.listeners = {}; this.value = ""; this.hidden = false; this.disabled = false; this.textContent = ""; this.className = ""; this.dataset = {}; this.attributes = {}; this.classList = { values: new Set(), add: (...v) => v.forEach(x => this.classList.values.add(x)), remove: (...v) => v.forEach(x => this.classList.values.delete(x)), toggle: (v, force) => { const on = force ?? !this.classList.values.has(v); this.classList.values[on ? "add" : "delete"](v); return on; }, contains: v => this.classList.values.has(v) }; }
  addEventListener(type, handler) { (this.listeners[type] ??= []).push(handler); }
  click() { for (const handler of this.listeners.click ?? []) handler({ target: this, preventDefault() {} }); }
  append(...items) { this.children.push(...items); }
  appendChild(item) { this.children.push(item); return item; }
  replaceChildren(...items) { this.children = items; }
  querySelector() { return null; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  scrollIntoView() {}
}

function installDom(search) {
  const nodes = new Map(ids.map(id => [id, new Element(id)]));
  const body = new Element("body");
  const modeButtons = ["training", "exam"].map(mode => {
    const button = new Element(); button.dataset.learningMode = mode; return button;
  });
  globalThis.document = {
    body,
    getElementById: id => nodes.get(id),
    querySelectorAll: selector => selector === "[data-learning-mode]" ? modeButtons : [],
    createElement: () => new Element(),
    createTextNode: text => ({ textContent: text }),
  };
  globalThis.location = { search, protocol: "http:", host: "test" };
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { clipboard: { writeText: async () => {} } },
  });
  globalThis.WebSocket = { OPEN: 1 };
  return { nodes, body, modeButtons };
}

const caseItem = { id: "case-1", version: "1", title: "Douleur", public_summary: "Résumé", language: "de-DE", mode: "fsp" };
const activeExam = {
  id: "session-1", learner_id: "learner-1", case_id: "case-1", case_version: "1", training_snapshot: {},
  voice_stack_id: "pipeline_economy", voice_transport: "pipeline", voice_stack_available: true,
  interaction_mode: "guided", voice_profile: "economy", learning_mode: "exam", status: "active",
  turns: [{ user_text: "Hallo", patient_text: "Guten Tag", provider_response_status: "completed" }], patient_opening: null,
};

async function load(search, responses) {
  const dom = installDom(search);
  const calls = [];
  globalThis.fetch = async path => {
    calls.push(String(path));
    const body = responses[String(path)];
    return { ok: body !== undefined, status: body === undefined ? 404 : 200, json: async () => body };
  };
  await import(`${appUrl}?dom-test=${encodeURIComponent(search)}-${Math.random()}`);
  await new Promise(resolve => setTimeout(resolve, 0));
  return { ...dom, calls };
}

const health = { provider_mode: "fake", voice_transport: "pipeline", technical_test_enabled: false };

{
  const result = await load("?session=session-1", {
    "/api/health": health, "/api/cases?approved_only=true": [caseItem], "/api/sessions/session-1": activeExam,
  });
  assert.equal(result.body.classList.contains("exam-active"), true);
  assert.equal(result.nodes.get("transcript").children.length, 0, "exam restores no transcript nodes");
  assert.equal(result.calls.some(path => path.includes("vocabulary-hints")), false);
}

{
  const completed = {
    ...activeExam,
    status: "completed",
    evaluation: {
      schema_version: "session-evaluation-v2", summary: "Retour", criteria: [], strengths: [], priorities: [],
    },
    vocabulary: [],
  };
  const result = await load("?session=session-1", {
    "/api/health": health, "/api/cases?approved_only=true": [caseItem], "/api/sessions/session-1": completed,
  });
  assert.equal(result.nodes.get("transcript").children.length > 0, true, "feedback restores the transcript after completion");
  assert.equal(result.nodes.get("feedback").classList.contains("hidden"), false);
}

{
  const result = await load("?case=case-1&mode=training", {
    "/api/health": health, "/api/cases?approved_only=true": [caseItem],
  });
  const subtitles = result.nodes.get("subtitles-toggle");
  assert.equal(subtitles.hidden, false);
  subtitles.click();
  assert.equal(result.body.classList.contains("captions-off"), true);
  assert.equal(subtitles.getAttribute("aria-pressed"), "false");
}

{
  const result = await load("?case=missing&mode=exam", {
    "/api/health": health, "/api/cases?approved_only=true": [caseItem],
  });
  assert.equal(result.nodes.get("start").disabled, true, "invalid explicit case does not fall back");
  assert.equal(result.calls.some(path => path.startsWith("/api/sessions/")), false);
}
