// Remediation drill client ("Votre phrase"). Start and every answer carry an idempotency key
// persisted per profile, drill and item, so a lost response or a reload never counts twice.
export class LanguageDrillClient {
  constructor(api, storage = globalThis.sessionStorage, uuid = () => globalThis.crypto.randomUUID()) {
    this.api = api; this.storage = storage; this.uuid = uuid; this.profileId = null;
  }
  key(...parts) { return ["ari:drill", this.profileId, ...parts].join(":"); }
  overview() { return this.api("/api/language-drill"); }
  get(id) { return this.api(`/api/language-drill/drills/${encodeURIComponent(id)}`); }
  async start() {
    const key = this.key("start");
    let requestId = this.storage.getItem(key);
    if (!requestId) { requestId = this.uuid(); this.storage.setItem(key, requestId); }
    const drill = await this.api("/api/language-drill/drills", { method: "POST", body: JSON.stringify({ request_id: requestId }) });
    this.storage.removeItem(key);
    return drill;
  }
  async answer(drill, itemId, text) {
    const clean = String(text || "").trim();
    if (!clean) throw new Error("Écrivez votre phrase avant de vérifier.");
    const key = this.key(drill.id, itemId);
    let eventId = this.storage.getItem(key);
    if (!eventId) { eventId = this.uuid(); this.storage.setItem(key, eventId); }
    const result = await this.api(`/api/language-drill/drills/${encodeURIComponent(drill.id)}/answers`, {
      method: "POST", body: JSON.stringify({ event_id: eventId, item_id: itemId, text: clean }),
    });
    this.storage.removeItem(key);
    return result;
  }
  finish(drill) { return this.api(`/api/language-drill/drills/${encodeURIComponent(drill.id)}/finish`, { method: "POST" }); }
}

// What the learner reads after an answer: never a failure, and the correction is only the one
// proposed when the session was analysed.
export function outcomeText(outcome) {
  return outcome === "matched"
    ? "C’est la forme corrigée proposée lors de l’analyse de votre séance."
    : "Ce n’est pas identique à la correction proposée. Comparez avec la forme corrigée : une autre formulation correcte peut exister.";
}

export function resultText(result) {
  if (!result) return "";
  if (result.answered === 0) return "Aucune phrase traitée.";
  if (result.matched === 0) return `Aucune des ${result.answered} phrase${result.answered > 1 ? "s" : ""} n’est identique à la correction proposée : comparez-les ci-dessous.`;
  return `${result.matched} phrase${result.matched > 1 ? "s" : ""} sur ${result.answered} ${result.matched > 1 ? "reprises" : "reprise"} comme la correction proposée.`;
}
