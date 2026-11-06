import assert from "node:assert/strict";
import { LanguageExercisesClient, REPORT_REASONS, categoryLine, seriesText, verdictText } from "../language-exercises-client.mjs";

const values = new Map();
const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
let ids = 0, failing = false;
const calls = [];
const api = async (path, options = {}) => {
  calls.push({ path, options });
  if (failing) throw new TypeError("Network unavailable");
  return { correct: true };
};
const client = new LanguageExercisesClient(api, storage, () => `id-${++ids}`);
client.profileId = "alice";
const exercise = { id: "lx-1" };

// One request id per exercise, replayed after a lost response, then forgotten.
await assert.rejects(() => client.answer(exercise, undefined), /Choisissez/);
failing = true;
await assert.rejects(() => client.answer(exercise, 2));
const body = JSON.parse(calls.at(-1).options.body);
failing = false;
await client.answer(exercise, 2);
assert.deepEqual(JSON.parse(calls.at(-1).options.body), body);
assert.deepEqual([body.exercise_id, body.chosen_index], ["lx-1", 2]);
assert.equal(values.size, 0);
await client.answer(exercise, 0);
assert.notEqual(JSON.parse(calls.at(-1).options.body).request_id, body.request_id, "a new answer is a new request");

// The category is escaped, the report sends a reason and an optional comment.
await client.next("word_order");
assert.equal(calls.at(-1).path, "/api/language-exercises/next?category=word_order");
await client.report(exercise, "wrong_key", "   ");
assert.deepEqual(JSON.parse(calls.at(-1).options.body), { reason: "wrong_key", comment: null });
await client.report(exercise, "other", " der ou die ? ");
assert.deepEqual(JSON.parse(calls.at(-1).options.body), { reason: "other", comment: "der ou die ?" });
assert.deepEqual(REPORT_REASONS.map(r => r.value), ["wrong_key", "several_answers", "unclear", "other"]);

assert.equal(verdictText({ correct: true }), "Juste.");
assert.match(verdictText({ correct: false }), /réponse attendue/);
assert.equal(seriesText([]), "Aucun exercice fait.");
assert.equal(seriesText([{ correct: true }, { correct: false }]), "1 réponse juste sur 2.");
assert.equal(seriesText([{ correct: true }, { correct: true }]), "2 réponses justes sur 2.");
assert.equal(categoryLine({ label: "les cas", level: "B2", available: 1 }), "Les cas · niveau B2 · 1 exercice");
assert.match(categoryLine({ label: "le genre", level: "B1", available: 0 }), /rien de neuf/);
console.log("language-exercises-client ok");
