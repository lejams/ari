// Placement test client. Start and every answer carry an idempotency key persisted per
// profile, attempt and item, so a lost response or a reload never counts an answer twice.
export const LEVEL_LABELS = { A1: "A1 · découverte", A2: "A2 · survie", B1: "B1 · seuil", B2: "B2 · indépendant", C1: "C1 · autonome", C2: "C2 · maîtrise" };
export const PHASE_LABELS = { mcq: "Vocabulaire et grammaire", listening: "Compréhension orale", speaking: "Expression orale", completed: "Terminé" };

export class PlacementClient {
  constructor(api, storage = globalThis.sessionStorage, uuid = () => globalThis.crypto.randomUUID()) {
    this.api = api; this.storage = storage; this.uuid = uuid; this.profileId = null;
  }
  key(...parts) { return ["ari:placement", this.profileId, ...parts].join(":"); }
  overview() { return this.api("/api/placement"); }
  get(id) { return this.api(`/api/placement/attempts/${encodeURIComponent(id)}`); }
  async start() {
    if (!this.profileId) throw new Error("Créez ou retrouvez votre profil avant de commencer.");
    const key = this.key("start");
    let requestId = this.storage.getItem(key);
    if (!requestId) { requestId = this.uuid(); this.storage.setItem(key, requestId); }
    const attempt = await this.api("/api/placement/attempts", { method: "POST", body: JSON.stringify({ request_id: requestId }) });
    this.storage.removeItem(key);
    return attempt;
  }
  eventFor(attempt, itemId) {
    const key = this.key(attempt.id, itemId);
    let eventId = this.storage.getItem(key);
    if (!eventId) { eventId = this.uuid(); this.storage.setItem(key, eventId); }
    return { key, eventId };
  }
  async answer(attempt, itemId, optionIndex) {
    if (!Number.isInteger(optionIndex) || optionIndex < 0) throw new Error("Choisissez une réponse.");
    const { key, eventId } = this.eventFor(attempt, itemId);
    const result = await this.api(`/api/placement/attempts/${encodeURIComponent(attempt.id)}/answers`, {
      method: "POST", body: JSON.stringify({ event_id: eventId, item_id: itemId, option_index: optionIndex }),
    });
    this.storage.removeItem(key);
    return result;
  }
  // `audio` is PCM16 mono 24 kHz bytes; `text` is only accepted by the server in fake mode.
  async speak(attempt, itemId, { audio = null, text = null } = {}) {
    if (!audio && !text) throw new Error("Enregistrez votre réponse avant d’envoyer.");
    const { key, eventId } = this.eventFor(attempt, itemId);
    // Transcription plus rating of a one-minute recording can take well over the default 20 s.
    const result = await this.api(`/api/placement/attempts/${encodeURIComponent(attempt.id)}/speaking/${encodeURIComponent(itemId)}`, {
      method: "POST",
      body: audio || text,
      headers: { "Content-Type": audio ? "application/octet-stream" : "text/plain", "X-Event-Id": eventId },
      signal: AbortSignal.timeout(180000),
    });
    this.storage.removeItem(key);
    return result;
  }
  finish(attempt) { return this.api(`/api/placement/attempts/${encodeURIComponent(attempt.id)}/finish`, { method: "POST" }); }
  audioUrl(attempt, itemId) { return `/api/placement/attempts/${encodeURIComponent(attempt.id)}/items/${encodeURIComponent(itemId)}/audio`; }
}

export function describeResult(result) {
  if (!result) return "";
  const parts = [
    `Vocabulaire et grammaire : ${result.vocab_grammar_level}`,
    `Compréhension orale : ${result.listening_level}`,
    result.speaking_level
      ? `Expression orale : ${result.speaking_level}${result.speaking_counted ? "" : " (confiance insuffisante, non comptée)"}`
      : "Expression orale : non évaluée",
  ];
  return parts.join(" · ");
}
