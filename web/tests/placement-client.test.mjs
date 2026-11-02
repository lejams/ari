import assert from "node:assert/strict";
import { PlacementClient, describeResult, describeWeakPoints } from "../placement-client.mjs";

const values = new Map();
const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
let ids = 0, failing = false;
const calls = [];
const api = async (path, options = {}) => {
  calls.push({ path, options });
  if (failing) throw new TypeError("Network unavailable");
  return { id: "attempt-1", phase: "mcq", current_item: { id: "a1-1" } };
};
const client = new PlacementClient(api, storage, () => `id-${++ids}`);
await assert.rejects(() => client.start(), /profil/);
client.profileId = "alice";

// The start request survives a lost response with the same request id.
failing = true;
await assert.rejects(() => client.start());
const startBody = calls.at(-1).options.body;
failing = false;
const attempt = await client.start();
assert.equal(calls.at(-1).options.body, startBody);
assert.equal(values.size, 0);

// One event id per attempt and item, replayed after a failure, then forgotten.
await assert.rejects(() => client.answer(attempt, "a1-1", -1), /Choisissez/);
failing = true;
await assert.rejects(() => client.answer(attempt, "a1-1", 2));
const answerBody = JSON.parse(calls.at(-1).options.body);
failing = false;
await client.answer(attempt, "a1-1", 2);
assert.deepEqual(JSON.parse(calls.at(-1).options.body), answerBody);
assert.equal(answerBody.option_index, 2);
assert.equal(values.size, 0);

// Speaking sends raw PCM with the event id in a header; text is the fake-mode fallback.
const audio = new Uint8Array([1, 2, 3]);
await client.speak(attempt, "s-1", { audio });
assert.equal(calls.at(-1).options.headers["Content-Type"], "application/octet-stream");
assert.match(calls.at(-1).options.headers["X-Event-Id"], /^id-/);
assert.equal(calls.at(-1).options.body, audio);
await client.speak(attempt, "s-2", { text: "Ich heiße Anna." });
assert.equal(calls.at(-1).options.headers["Content-Type"], "text/plain");
await assert.rejects(() => client.speak(attempt, "s-2", {}), /Enregistrez/);
assert.equal(client.audioUrl(attempt, "l-a1"), "/api/placement/attempts/attempt-1/items/l-a1/audio");

// Profiles never share pending keys.
failing = true;
await assert.rejects(() => client.answer(attempt, "b1-1", 0));
const aliceEvent = JSON.parse(calls.at(-1).options.body).event_id;
client.profileId = "bob";
await assert.rejects(() => client.answer(attempt, "b1-1", 0));
assert.notEqual(JSON.parse(calls.at(-1).options.body).event_id, aliceEvent);

assert.match(describeResult({ vocab_grammar_level: "B1", listening_level: "B2", speaking_level: "A2", speaking_counted: false }), /non comptée/);
assert.match(describeResult({ vocab_grammar_level: "B1", listening_level: "B1", speaking_level: null }), /non évaluée/);
// A flagged question names the attempt and the item; weak points are described from the computed result only.
failing = false;
await client.report(attempt, "b1-1", "deux réponses possibles");
assert.equal(calls.at(-1).path, "/api/placement/attempts/attempt-1/report");
assert.deepEqual(JSON.parse(calls.at(-1).options.body), { item_id: "b1-1", note: "deux réponses possibles" });
assert.deepEqual(describeWeakPoints({}), []);
const points = describeWeakPoints({ weak_points: [
  { category: "case", signals: ["written"], written_answered: 3, written_wrong: 2, spoken_errors: 0, examples: [] },
  { category: "verb_form", signals: ["written", "spoken"], written_answered: 2, written_wrong: 1, spoken_errors: 2, examples: [{ quote: "ich habe gegangen", correction: "ich bin gegangen" }] },
] });
assert.match(points[0].label, /^Cas/);
assert.equal(points[0].evidence, "2 réponses fausses sur 3 questions");
assert.equal(points[1].evidence, "1 réponse fausse sur 2 questions · 2 erreurs à l’oral");
assert.equal(points[1].examples[0].correction, "ich bin gegangen");
console.log("Placement client: idempotent start, answers, speaking upload and identity passed");
