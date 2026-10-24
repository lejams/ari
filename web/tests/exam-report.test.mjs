import assert from "node:assert/strict";
import { examReportView, formatDuration, formatSeconds } from "../exam-report.mjs";

const turn = (overrides) => ({
  sequence: 1, response_kind: "sources", answered: true, heard: true, audio_failed: false,
  facts_selected: 1, facts_heard: 1, free_text: null, response_ms: 1200, ...overrides,
});
const report = (overrides) => ({
  schema_version: "exam-report-v1", voice_stack_id: "chain_exam", duration_seconds: 754,
  turns: [
    turn({}),
    turn({ sequence: 2, heard: false, facts_selected: 2, facts_heard: 0, response_ms: null }),
    turn({ sequence: 3, response_kind: "unknown", facts_selected: 0, facts_heard: 0, free_text: "rejected", response_ms: 2400 }),
    turn({ sequence: 4, answered: false, heard: false, facts_selected: 0, facts_heard: 0, response_ms: null }),
    turn({ sequence: 5, heard: false, audio_failed: true, facts_selected: 1, facts_heard: 0, response_ms: null }),
  ],
  turn_count: 5, heard_turns: 2, not_heard_turns: 2, audio_failures: 1, free_text_rejections: 1,
  transcription_failures: 2, facts_heard: 1, facts_not_heard: 3, response_ms_p50: 1200, response_ms_p95: 2400,
  ...overrides,
});

assert.equal(formatSeconds(1234), "1,2 s");
assert.equal(formatSeconds(null), null);
assert.equal(formatDuration(754), "12 min 34 s");
assert.equal(formatDuration(600), "10 min");
assert.equal(formatDuration(42), "42 s");
assert.equal(formatDuration(null), null);

// No report (training, unfinished session) or an unknown schema: nothing is shown.
assert.equal(examReportView(null), null);
assert.equal(examReportView(undefined), null);
assert.equal(examReportView(report({ schema_version: "exam-report-v2" })), null);

const view = examReportView(report({}));
assert.equal(view.count, "(2/4 entendues)");
assert.deepEqual(view.summary, [
  "Durée de l’appel : 12 min 34 s",
  "Réponses du patient entendues : 2 sur 4 (2 non entendues)",
  "Faits du cas entendus : 1 · 3 faits prévus mais non entendus",
  "Incidents techniques : 1 échec audio · 2 transcriptions en échec · 1 formulation libre refusée",
  "Temps de réponse du serveur : médiane 1,2 s · 95 % des tours sous 2,4 s",
]);
assert.deepEqual(view.turns, [
  "Tour 1 · réponse sur le cas · entendue · faits entendus 1/1 · 1,2 s",
  "Tour 2 · réponse sur le cas · non entendue (interrompue ou non confirmée) · faits entendus 0/2",
  "Tour 3 · le patient ne sait pas · entendue · formulation libre refusée · 2,4 s",
  "Tour 4 · pas de réponse du patient",
  "Tour 5 · réponse sur le cas · échec audio · faits entendus 0/1",
]);

// A clean session without timings (a call is missing): unknown stays visible, never zero.
const clean = examReportView(report({
  duration_seconds: null, turns: [turn({ response_ms: null })], turn_count: 1, heard_turns: 1, not_heard_turns: 0,
  audio_failures: 0, free_text_rejections: 0, transcription_failures: 0, facts_not_heard: 0,
  response_ms_p50: null, response_ms_p95: null,
}));
assert.deepEqual(clean.summary, [
  "Réponses du patient entendues : 1 sur 1",
  "Faits du cas entendus : 1",
  "Incidents techniques : aucun",
  "Temps de réponse du serveur : non mesuré pour cette session",
]);
assert.deepEqual(clean.turns, ["Tour 1 · réponse sur le cas · entendue · faits entendus 1/1"]);
