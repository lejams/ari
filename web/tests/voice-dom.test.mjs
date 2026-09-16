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
      schema_version: "session-evaluation-v4", summary: "Retour", strengths: [], priorities: [],
      criteria: [{ criterion_id: "clinical_coverage", label: "Couverture clinique", max_score: 5, score: 4.166667, evidence_turn_sequences: [1], feedback: "5/6" }],
      language_errors: [], code_switches: [{ turn: 1, fragment: "la douleur", intended_term: "der Schmerz" }],
      structure: {
        version: "anamnesis-sections-v1", covered_count: 1, total_count: 2, order_observed: ["patientendaten"], canonical_order_respected: true,
        sections: [
          { id: "patientendaten", label: "Patientendaten", fact_ids: ["identity"], covered_fact_ids: ["identity"], missing_fact_ids: [], first_turn: 1 },
          { id: "noxen", label: "Noxen", fact_ids: ["smoking"], covered_fact_ids: [], missing_fact_ids: ["smoking"], first_turn: null },
        ],
      },
      empathy: [{ moment_id: "m1", cue: "décès du père", expected: "reconnaître", trigger_turn: 1, response_turn: 2, verdict: "ignored", feedback: "Vous avez enchaîné.", evidence_turn_sequences: [2] }],
      next_actions: [{ kind: "uncovered_section", text: "Couvrez la section « Noxen ».", target: { kind: "section", id: "noxen" } }],
    },
    vocabulary: [{ lemma: "ausstrahlen", translation: "irradier", kind: "missing", evidence_turn_sequences: [1] }],
    lexicon: { added: [{ lemma: "der Schmerz", translation: "la douleur", state: "identified" }], promoted: [], wrong_language_turns: [1] },
  };
  const result = await load("?session=session-1", {
    "/api/health": health, "/api/cases?approved_only=true": [caseItem], "/api/sessions/session-1": completed,
  });
  assert.equal(result.nodes.get("transcript").children.length > 0, true, "feedback restores the transcript after completion");
  assert.equal(result.nodes.get("feedback").classList.contains("hidden"), false);
  assert.match(result.nodes.get("code-switches").children[0].textContent, /la douleur.*der Schmerz/);
  assert.match(result.nodes.get("vocabulary").children[0].textContent, /mot manquant/);
  assert.match(result.nodes.get("lexicon-summary").textContent, /1 mot ajouté/);
  assert.equal(result.nodes.get("retry-case").classList.contains("hidden"), false, "the learner can redo the same case");
  assert.equal(result.nodes.get("next-actions-block").classList.contains("hidden"), false);
  assert.match(result.nodes.get("next-actions").children[0].textContent, /Noxen/);
  assert.match(result.nodes.get("structure").children[0].textContent, /1 section complète sur 2/);
  assert.match(result.nodes.get("structure").children[1].children[1].textContent, /—.*Noxen.*manque : smoking/);
  const moment = result.nodes.get("empathy").children[0].children[0];
  assert.match(moment.textContent, /décès du père : Pas de réaction/);
  assert.equal(moment.children.find((child) => child.className === "turn-ref")?.textContent, "tour 2", "the cited turn is a link");
  // Tiles summarise each area with a state, and the details carry counts.
  assert.equal(result.nodes.get("tile-structure").dataset.state, "warn");
  assert.equal(result.nodes.get("tile-empathy").dataset.state, "bad");
  assert.match(result.nodes.get("tile-empathy-note").textContent, /Pas de réaction \(tour 2\)/);
  assert.equal(result.nodes.get("tile-language").dataset.state, "warn");
  assert.match(result.nodes.get("tile-language-note").textContent, /1 passage en autre langue/);
  assert.equal(result.nodes.get("tile-lexicon").dataset.state, "warn");
  assert.equal(result.nodes.get("code-switches-count").textContent, "(1)");
  assert.equal(result.nodes.get("language-errors-count").textContent, "(0)");
  // Dimensions use the rubric label and a rounded score; the transcript marks cited turns.
  const dimension = result.nodes.get("voice-dimensions").children[0];
  assert.equal(dimension.children[0].textContent, "Couverture clinique");
  assert.equal(dimension.children[2].textContent, "4,2 / 5");
  assert.equal(result.nodes.get("feedback-transcript").children.length, 1);
  assert.equal(result.nodes.get("feedback-transcript").children[0].className, "fb-turn cited");
  assert.match(result.nodes.get("feedback-meta").textContent, /Douleur · Examen · 1 tour/);
}

{
  // Legacy v2 feedback and a session without a lexicon report still render.
  const legacy = {
    ...activeExam, status: "completed", vocabulary: [], lexicon: null,
    evaluation: { schema_version: "session-evaluation-v2", summary: "Retour", criteria: [], strengths: [], priorities: [] },
  };
  const result = await load("?session=session-1", {
    "/api/health": health, "/api/cases?approved_only=true": [caseItem], "/api/sessions/session-1": legacy,
  });
  assert.equal(result.nodes.get("feedback").classList.contains("hidden"), false);
  assert.match(result.nodes.get("lexicon-summary").textContent, /indisponible/);
  assert.equal(result.nodes.get("voice-dimensions").children.length, 0, "v2 stays comparable, no legacy notice");
  assert.equal(result.nodes.get("next-actions-block").classList.contains("hidden"), true, "no actions, no block");
  assert.match(result.nodes.get("structure").children[0].textContent, /ne définit pas de sections/);
  assert.match(result.nodes.get("empathy").children[0].textContent, /ne définit pas de moment/);
  assert.equal(result.nodes.get("tile-structure").dataset.state, "none");
  assert.equal(result.nodes.get("tile-lexicon").dataset.state, "none");
  assert.equal(result.nodes.get("tile-language").dataset.state, "ok");
  assert.match(result.nodes.get("language-errors").children[0].textContent, /Aucune erreur/);
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
