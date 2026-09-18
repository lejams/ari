import assert from "node:assert/strict";
import {BackofficeApi, blockers, statusLabel} from "../api.mjs";

const calls = [];
const fetcher = async (path, options) => {
  calls.push({path, options});
  if (path === "/api/auth/me") return {ok: false, status: 401, json: async () => ({detail: "Connexion requise"})};
  if (path === "/api/protocols/P-1/versions") return {ok: false, status: 422, json: async () => ({detail: [{loc: ["anamnesis", 0, "polarity"], msg: "invalide", type: "value_error"}]})};
  if (path === "/api/auth/logout") return {ok: true, status: 204, json: async () => null};
  return {ok: true, status: 200, json: async () => ({items: [], total: 0, path})};
};
const client = new BackofficeApi(fetcher);

assert.equal(await client.restore(), null, "an anonymous visitor has no account");
assert.equal(calls[0].options.credentials, "same-origin");
assert.equal(calls[0].options.cache, "no-store");
assert.equal(calls[0].options.headers["Content-Type"], "application/json");

const file = new Blob(["%PDF-1.7"], {type: "application/pdf"}); file.name = "p.pdf";
await client.upload(file, {land: "Bayern", city: "", provenance: "x", consent_declaration: "y"});
const upload = calls.at(-1);
assert.ok(upload.options.body instanceof FormData, "uploads send multipart form data");
assert.equal(upload.options.headers["Content-Type"], undefined, "the browser sets the multipart boundary");
assert.deepEqual([...upload.options.body.keys()].sort(), ["consent_declaration", "file", "land", "provenance"], "empty fields are not sent");

await assert.rejects(() => client.revise("P-1", 1, "a".repeat(64), {}), error => error.status === 422 && /anamnesis\.0\.polarity : invalide/.test(error.message));
await client.protocols({status: "doctor_review", land: "", document: "d"});
assert.equal(calls.at(-1).path, "/api/protocols?status=doctor_review&document=d");
assert.equal(await client.logout(), undefined);
assert.equal(client.has("owner"), false);

const record = {
  location: {land: null}, patient: {uncertainty: null}, diagnosis: {uncertainty: "?"}, arzt_arzt: {}, outcome: {},
  anamnesis: [{id: "a01", uncertainty: "Nicht erwähnt"}], examiner_questions: [], field_uncertainties: [{path: "outcome.result", reason_fr: "r", resolution: null}],
  unresolved_questions: [{id: "u01", critical: true, answer: null}, {id: "u02", critical: false, answer: null}], pedagogy: {difficulty: null},
};
const doctor = blockers(record, "doctor");
assert.deepEqual(doctor, [
  "Incertitude ouverte: diagnosis: ?",
  "Incertitude ouverte: anamnesis.a01: Nicht erwähnt",
  "Incertitude ouverte: outcome.result: r",
  "Question critique sans réponse: u01",
  "Difficulté non renseignée",
]);
assert.deepEqual(blockers(record, "owner").at(-1), "Land manquant");
assert.equal(statusLabel("doctor_review"), "À relire");
console.log("Back-office client: same-origin JSON, multipart upload, 422 mapping, checklist mirror passed");
