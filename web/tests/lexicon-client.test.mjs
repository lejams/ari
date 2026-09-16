import assert from "node:assert/strict";
import {LexiconClient, normalizeAnswer, lexiconStateLabel} from "../lexicon-client.mjs";

const values = new Map();
const storage = {getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key)};
let ids = 0, failing = false;
const calls = [];
const api = async (path, options = {}) => {
  calls.push({path, options});
  if (failing) throw new TypeError("Network unavailable");
  return {id: "entry-1", state: "reviewed"};
};
const client = new LexiconClient(api, storage, () => `evt-${++ids}`);
client.profileId = "alice";
const entry = {id: "entry-1", lemma: "die Übelkeit"};

// A lost response keeps the same event id, so the server counts one review.
failing = true;
await assert.rejects(() => client.review(entry, "good"));
const first = calls.at(-1).options.body;
await assert.rejects(() => client.review(entry, "easy"), /confirmation/);
failing = false;
await client.review(entry, "good");
assert.equal(calls.at(-1).options.body, first);
assert.equal(values.size, 0);
assert.match(calls.at(-1).path, /\/api\/lexicon\/entries\/entry-1\/reviews$/);
await assert.rejects(() => client.review(entry, "perfect"), /inconnue/);

// Typed answers are compared lexically: case, spacing and final punctuation are ignored.
assert.equal(normalizeAnswer("  Die  Übelkeit. "), "die übelkeit");
assert.equal(LexiconClient.suggest(entry, "DIE ÜBELKEIT!"), "good");
assert.equal(LexiconClient.suggest(entry, "der Husten"), "again");
assert.equal(LexiconClient.suggest(entry, "   "), null);
assert.throws(() => client.add("   "), /80 caractères/);
assert.equal(lexiconStateLabel("mastered"), "Maîtrisé");

// Profiles never share a pending event.
failing = true;
await assert.rejects(() => client.review(entry, "good"));
const aliceEvent = JSON.parse(calls.at(-1).options.body).event_id;
client.profileId = "bob";
await assert.rejects(() => client.review(entry, "good"));
assert.notEqual(JSON.parse(calls.at(-1).options.body).event_id, aliceEvent);
console.log("Lexicon client: idempotent reviews, normalisation and identity passed");
