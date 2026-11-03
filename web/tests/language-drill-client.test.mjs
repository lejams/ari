import assert from "node:assert/strict";
import { LanguageDrillClient, outcomeText, resultText } from "../language-drill-client.mjs";

const values = new Map();
const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
let ids = 0, failing = false;
const calls = [];
const api = async (path, options = {}) => {
  calls.push({ path, options });
  if (failing) throw new TypeError("Network unavailable");
  return { id: "drill-1", items: [{ id: "i-1" }] };
};
const client = new LanguageDrillClient(api, storage, () => `id-${++ids}`);
client.profileId = "alice";

// The start request survives a lost response with the same request id, then is forgotten.
failing = true;
await assert.rejects(() => client.start());
const startBody = calls.at(-1).options.body;
failing = false;
const drill = await client.start();
assert.equal(calls.at(-1).options.body, startBody);
assert.equal(values.size, 0);

// One event id per drill and item, replayed after a failure, then forgotten.
await assert.rejects(() => client.answer(drill, "i-1", "   "), /Écrivez/);
failing = true;
await assert.rejects(() => client.answer(drill, "i-1", " mit dem Arzt "));
const body = JSON.parse(calls.at(-1).options.body);
failing = false;
await client.answer(drill, "i-1", "mit dem Arzt");
assert.deepEqual(JSON.parse(calls.at(-1).options.body), body);
assert.deepEqual([body.item_id, body.text], ["i-1", "mit dem Arzt"]);
assert.equal(values.size, 0);

// A miss is never called a failure.
assert.match(outcomeText("matched"), /corrigée proposée/);
assert.doesNotMatch(outcomeText("not_matched"), /faux|erreur|échec/i);
assert.match(outcomeText("not_matched"), /autre formulation correcte/);
assert.equal(resultText({ matched: 1, answered: 3, total: 5 }), "1 phrase sur 3 reprise comme la correction proposée.");
assert.equal(resultText({ matched: 2, answered: 2, total: 2 }), "2 phrases sur 2 reprises comme la correction proposée.");
assert.equal(resultText({ matched: 0, answered: 0, total: 2 }), "Aucune phrase traitée.");
assert.match(resultText({ matched: 0, answered: 3, total: 3 }), /^Aucune des 3 phrases n’est identique/);
console.log("language-drill-client ok");
