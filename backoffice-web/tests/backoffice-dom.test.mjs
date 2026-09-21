// Loads app.mjs against a minimal DOM for every route, signed out and signed in, and fails when
// the page's own error handler caught anything.
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {pathToFileURL} from "node:url";

const moduleUrl = pathToFileURL(new URL("../app.mjs", import.meta.url).pathname).href;
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);

class Element {
  constructor(id = "", tagName = "DIV") {
    this.id = id; this.tagName = tagName; this.children = []; this.listeners = {}; this.value = ""; this.hidden = false; this.disabled = false;
    this.textContent = ""; this.className = ""; this.dataset = {}; this.attributes = {}; this.style = {}; this.files = []; this.checked = false;
    this.classList = {values: new Set(), add: (...v) => v.forEach(x => this.classList.values.add(x)), remove: (...v) => v.forEach(x => this.classList.values.delete(x)), toggle: (v, force) => { const on = force ?? !this.classList.values.has(v); this.classList.values[on ? "add" : "delete"](v); return on; }, contains: v => this.classList.values.has(v)};
  }
  addEventListener(type, handler) { (this.listeners[type] ??= []).push(handler); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  querySelector() { return null; }
  querySelectorAll() { return []; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  reset() {}
  focus() {}
}

function installDom(hash) {
  const nodes = new Map(ids.map(id => [id, new Element(id, id.includes("status") || id.endsWith("-land") ? "SELECT" : "DIV")]));
  nodes.get("review-status").value = "doctor_review"; // A real <select> starts on its `selected` option.
  nodes.get("registry-status").value = "draft_unvalidated";
  globalThis.document = {
    getElementById: id => nodes.get(id) ?? null,
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: tag => new Element("", tag.toUpperCase()),
  };
  globalThis.window = globalThis;
  globalThis.location = {hash, href: "http://test/"};
  globalThis.scrollTo = () => {};
  globalThis.addEventListener = () => {};
  return nodes;
}

const me = {id: "owner-1", display_name: "Owner", roles: ["owner", "physician_reviewer"]};
const record = {
  schema_version: "fsp-protocol-v1", language: "de-DE",
  source: {document_id: "c".repeat(64), segment_index: 0, page_from: 2, page_to: 2, title_hint: null, date_hint: null, land_hint: null, city_hint: null},
  location: {land: "Bayern", city: null, exam_body: null, exam_date: "2026-03", specialty: null},
  patient: {age_years: 54, sex: "female", occupation_de: null, presenting_complaint_de: "Thoraxschmerz", summary_de: null, uncertainty: null},
  anamnesis: [{id: "a01", section: "aktuelle_beschwerden", label_de: "Schmerz", value_de: "Drückend", polarity: "present", temporality: null, quote_de: null, source_pages: [2], uncertainty: null},
    {id: "a02", section: "allergien", label_de: "Allergien", value_de: null, polarity: "unknown", temporality: null, quote_de: null, source_pages: [], uncertainty: "Nicht erwähnt"}],
  diagnosis: {suspected_de: null, differentials_de: [], uncertainty: null},
  examiner_questions: [{id: "q01", part: "arzt_arzt", question_de: "Stellen Sie vor.", expected_answer_de: null, candidate_answer_de: null, source_pages: [], uncertainty: null}],
  arzt_arzt: {presentation_summary_de: null, discussion_points_de: [], uncertainty: null},
  fachbegriffe: [{id: "t01", german: "Dyspnoe", lay_german: "Atemnot", french: null, asked: true}],
  arztbrief_notes_de: null,
  outcome: {result: "unknown", examiner_feedback_de: [], candidate_tips_de: [], uncertainty: null},
  unresolved_questions: [{id: "u01", text_fr: "Résultat connu ?", critical: true, answer: null}],
  field_uncertainties: [{path: "outcome.result", reason_fr: "Non indiqué", resolution: null}],
  pseudonymisation: {removed_kinds: ["examiner_name"], notes: []},
  pedagogy: {critical_pitfalls: [], pitfalls: [], difficulty: null, notes_fr: null},
};
const protocol = {
  id: "P-cccccccc-000", version: 1, content_hash: "a".repeat(64), status: "doctor_review", document_id: "c".repeat(64), segment_id: "s", page_from: 2, page_to: 2,
  land: "Bayern", specialty: null, title_hint: null, presenting_complaint_de: "Thoraxschmerz", open_doctor_blockers: 4, open_owner_blockers: 4, pii_findings: 1, difficulty: null, created_via: "ai", created_at: "2026-09-18T00:00:00Z",
  record, pii: [{kind: "person", path: "patient.summary_de", excerpt: "Fr…"}], pii_detector_version: "pii-detector-v1",
  blockers: {doctor: ["x"], owner: ["x"]}, open_uncertainties: [], diff_to_previous: [], diff_to_first: [],
  versions: [{version: 1, status: "doctor_review", created_via: "ai", created_by_account_id: null, created_at: "2026-09-18T00:00:00Z"}],
  reviews: [], events: [{id: "e", protocol_id: "P", protocol_version: 1, event_type: "extracted", actor: {kind: "system", name: "ari.worker", account_id: null}, payload: {}, created_at: "2026-09-18T00:00:00Z"}],
  document: {id: "c".repeat(64), filename: "p.pdf", declaration: {land: "Bayern", rights: "compatible"}}, gold: null,
};
const document_ = {
  document: {id: "c".repeat(64), filename: "p.pdf", page_count: 2, status: "extracted", created_at: "2026-09-18T00:00:00Z", declaration: {land: "Bayern", city: null, exam_body: null, exam_date: null, specialty: null, rights: "compatible", rights_evidence: "ok", provenance: "p", consent_declaration: "c", intended_use: "u"}},
  segments: [{id: "s", index: 0, page_from: 2, page_to: 2, start_marker: "Protokoll 1", confidence: 0.9, origin: "ai", status: "extracted", protocol: {id: "P-cccccccc-000", version: 1, status: "doctor_review"}}],
  jobs: {succeeded: 3}, last_error: null,
};
const gold = {
  protocol_id: "P-cccccccc-000", protocol_version: 2, protocol_hash: "b".repeat(64), location: {land: "Bayern", city: null, exam_body: null, exam_date: "2026-03", specialty: null},
  record: {...record, pedagogy: {critical_pitfalls: [{text_fr: "Oublier les allergies", related_item_ids: ["a02"]}], pitfalls: [], difficulty: "mittel", notes_fr: null}},
  document_id: "c".repeat(64), rights: "compatible", rights_evidence: "ok", frozen_at: "2026-09-19T00:00:00Z", frozen_by_account_id: "owner-1",
};
const draft = {
  id: "draft-1", gold_protocol_id: "P-cccccccc-000", gold_hash: "d".repeat(64), request: {phases: ["arzt_patient", "arzt_arzt"], persona_variant: "anxious", cefr: "B2", revision: 1},
  status: "draft", bundle_hash: "e".repeat(64), case_id: "FSP-BY-P-cccccccc-000", case_version: "1",
  scenario_refs: [{id: "FSP-BY-P-cccccccc-000-arzt_patient", version: "1", phase: "arzt_patient"}, {id: "FSP-BY-P-cccccccc-000-arzt_arzt", version: "1", phase: "arzt_arzt"}],
  validation_errors: [], ai_run_id: "run-1", created_by_account_id: "owner-1", created_at: "2026-09-20T00:00:00Z", imported_at: null,
};
const scenarioReport = {
  status: "draft_unvalidated", case_hash: "f".repeat(64), scenario_hash: "1".repeat(64),
  blockers: ["Approbation humaine clinical manquante pour le contenu exact", "Approbation humaine linguistic manquante pour le contenu exact"],
  reviews: [], markdown: "# Revue locale ARI",
  bundle: {cases: [{id: "FSP-BY-P-cccccccc-000", version: "1", title: "Fall: Thoraxschmerz", location: {land: "Bayern"}, facts: [{id: "a01"}, {id: "a02"}]}], scenarios: [{id: "FSP-BY-P-cccccccc-000-arzt_patient", version: "1", phase: "arzt_patient"}]},
};
const responses = {
  "/api/gold/P-cccccccc-000": gold,
  "/api/gold/P-cccccccc-000/bundle-drafts": {items: [draft], total: 1, jobs: [{id: "job-1", status: "queued", attempts: 0, last_error: null, request: draft.request, created_at: "2026-09-20T00:00:00Z"}], available_phases: ["arzt_patient", "arzt_arzt", "fachbegriffe"], next_revision: 2},
  "/api/registry/scenarios?status=draft_unvalidated": {items: [{id: "FSP-BY-P-cccccccc-000-arzt_patient", version: "1", status: "draft_unvalidated", phase: "arzt_patient", case_id: "FSP-BY-P-cccccccc-000", case_version: "1", case_hash: "f".repeat(64), scenario_hash: "1".repeat(64), title: "Fall: Thoraxschmerz", land: "Bayern", language: "de-DE"}], total: 1},
  "/api/registry/scenarios/FSP-BY-P-cccccccc-000-arzt_patient/1": scenarioReport,
  "/api/auth/me": me, "/api/meta/lands": ["Baden-Württemberg", "Bayern"],
  "/api/dashboard": {jobs_by_status: {succeeded: 3}, documents_by_status: {extracted: 1}, protocols_by_status: {doctor_review: 1}, review_queue: 1, owner_queue: 0, gold_by_land: {}, gold_total: 0, me},
  "/api/documents": {items: [document_.document], total: 1}, [`/api/documents/${"c".repeat(64)}`]: document_,
  [`/api/documents/${"c".repeat(64)}/pages/2`]: {page_number: 2, text: "Protokoll 1"},
  "/api/protocols?status=doctor_review": {items: [protocol], total: 1}, "/api/protocols/P-cccccccc-000": protocol,
  "/api/gold": {items: [], total: 0}, "/api/accounts": {items: [{id: "owner-1", email: "o@example.org", display_name: "Owner", roles: ["owner"], active: true, has_password: true, created_at: "2026-09-18T00:00:00Z"}], total: 1},
};

async function load(hash, {signedIn = true, roles = me.roles} = {}) {
  const nodes = installDom(hash);
  const calls = [];
  globalThis.fetch = async path => {
    calls.push(String(path));
    if (path === "/api/auth/me" && signedIn) return {ok: true, status: 200, json: async () => ({...me, roles})};
    if (path === "/api/auth/me" && !signedIn) return {ok: false, status: 401, json: async () => ({detail: "Connexion requise"})};
    const body = responses[String(path)];
    return {ok: body !== undefined, status: body === undefined ? 404 : 200, json: async () => body ?? {detail: `no stub for ${path}`}};
  };
  await import(`${moduleUrl}?dom-test=${encodeURIComponent(hash)}-${Math.random()}`);
  await new Promise(resolve => setTimeout(resolve, 30));
  return {nodes, calls};
}

{
  const {nodes} = await load("#/", {signedIn: false});
  assert.equal(nodes.get("error").hidden, true, nodes.get("error-text").textContent);
  assert.equal(nodes.get("login").hidden, false, "signed out visitors land on the login page");
  assert.equal(nodes.get("sidebar").hidden, true);
}
for (const [hash, section] of [["#/", "dashboard"], ["#/documents", "documents"], [`#/documents/${"c".repeat(64)}`, "document"], ["#/review", "review"], ["#/protocols/P-cccccccc-000", "protocol"], ["#/gold", "gold"], ["#/gold/P-cccccccc-000", "gold-detail"], ["#/registry", "registry"], ["#/registry/FSP-BY-P-cccccccc-000-arzt_patient/1", "scenario"], ["#/accounts", "accounts"]]) {
  const {nodes} = await load(hash);
  assert.equal(nodes.get("error").hidden, true, `${hash}: ${nodes.get("error-text").textContent}`);
  assert.equal(nodes.get(section).hidden, false, `${hash} shows ${section}`);
}
{
  const {nodes} = await load("#/protocols/P-cccccccc-000");
  assert.equal(nodes.get("record-sections").children.length, 12, "every part of the record has an editing section");
  const checklist = nodes.get("checklist").children.map(li => li.textContent);
  assert.deepEqual(checklist, [
    "Incertitude ouverte: anamnesis.a02: Nicht erwähnt",
    "Incertitude ouverte: outcome.result: Non indiqué",
    "Question critique sans réponse: u01",
    "Difficulté non renseignée",
  ]);
  assert.equal(nodes.get("pii-card").hidden, false, "PII findings are shown");
  assert.match(nodes.get("page-image").src, /pages\/2\/image$/);
  const actions = nodes.get("decision-actions").children.map(b => b.textContent);
  assert.deepEqual(actions, ["Enregistrer une version", "Approuver", "Demander des corrections", "Rejeter"], "a physician sees the doctor decisions");
}
{
  const {nodes} = await load("#/protocols/P-cccccccc-000", {roles: ["owner"]});
  const actions = nodes.get("decision-actions").children.map(b => b.textContent);
  assert.deepEqual(actions, ["Enregistrer une version"], "an owner without the physician role cannot approve a protocol in doctor review");
  assert.equal(nodes.get("decision-help").textContent, "Statut « À relire ». La décision revient à un compte « Relecteur médecin ». Votre compte : Propriétaire.");
}
{
  const {nodes} = await load("#/review");
  assert.equal(nodes.get("review-list").children.length, 1);
  assert.equal(nodes.get("review-land").children.length, 2, "the Land filter is filled from the server list");
}
{
  const {nodes} = await load("#/gold/P-cccccccc-000");
  assert.equal(nodes.get("draft-revision").value, "2", "the next revision is proposed");
  assert.equal(nodes.get("draft-jobs").children.length, 2, "one pending generation job listed");
  const row = nodes.get("draft-table").children[1];
  assert.equal(row.children[2].children[0].textContent, "Brouillon");
  assert.deepEqual(row.children[6].children[0].children.map(b => b.textContent), ["Importer dans le registre"], "an owner can import a validated draft");
}
{
  const {nodes} = await load("#/registry/FSP-BY-P-cccccccc-000-arzt_patient/1");
  assert.equal(nodes.get("scenario-title").textContent, "Fall: Thoraxschmerz");
  assert.equal(nodes.get("scenario-blockers").children.length, 2);
  const [publish] = nodes.get("scenario-actions").children;
  assert.equal(publish.textContent, "Publier aux apprenants");
  assert.equal(publish.disabled, true, "publication stays disabled while blockers remain");
  assert.deepEqual(nodes.get("review-type").children.map(o => o.value), ["clinical"], "an owner who is also a physician reviews clinically only");
  assert.equal(nodes.get("review-form").hidden, false);
}
{
  const {nodes} = await load("#/registry/FSP-BY-P-cccccccc-000-arzt_patient/1", {roles: ["linguistic_reviewer"]});
  assert.deepEqual(nodes.get("review-type").children.map(o => o.value), ["linguistic"]);
  assert.equal(nodes.get("scenario-actions").children.length, 0, "no publication for a reviewer");
}
console.log("Back-office DOM: login gate, every route renders, review screen, gold drafts and registry publication guard");
