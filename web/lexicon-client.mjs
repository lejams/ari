// Personal lexicon client. Review events are idempotent: the event id survives a lost
// response or a reload, so one rating never counts twice. Storage is scoped by profile.
export function normalizeAnswer(text) {
  return String(text).normalize("NFKC").toLowerCase().trim().replace(/\s+/g, " ").replace(/[.!? ]+$/, "");
}
export const RATINGS = ["again", "hard", "good", "easy"];
export const ratingLabel = rating => ({again: "Encore", hard: "Difficile", good: "Bien", easy: "Facile"}[rating] || rating);
export const lexiconStateLabel = state => ({identified: "À apprendre", reviewed: "Révisé", used: "Utilisé en session", mastered: "Maîtrisé"}[state] || state);
export const sourceLabel = source => ({evaluation_candidate: "Repéré dans votre feedback", terminology_unused: "Terme du cas non utilisé", code_switch: "Dit dans une autre langue", manual: "Ajouté par vous"}[source] || source);

export class LexiconClient {
  constructor(api, storage = globalThis.sessionStorage, uuid = () => globalThis.crypto.randomUUID()) {
    this.api = api; this.storage = storage; this.uuid = uuid; this.profileId = null;
  }
  overview() { return this.api("/api/lexicon"); }
  add(lemma, translation = "", example = "") {
    if (!lemma.trim() || lemma.length > 80) throw new Error("Saisissez un mot de 1 à 80 caractères.");
    return this.api("/api/lexicon/entries", {method: "POST", body: JSON.stringify({lemma: lemma.trim(), translation: translation.trim().slice(0, 120), example: example.trim().slice(0, 250)})});
  }
  archive(entry, archived) {
    return this.api(`/api/lexicon/entries/${encodeURIComponent(entry.id)}`, {method: "PATCH", body: JSON.stringify({archived})});
  }
  async review(entry, rating) {
    if (!RATINGS.includes(rating)) throw new Error("Évaluation inconnue");
    const key = `ari:review:${this.profileId}:${entry.id}`;
    let body = this.storage.getItem(key);
    if (!body) {
      body = JSON.stringify({event_id: this.uuid(), rating});
      this.storage.setItem(key, body);
    } else if (JSON.parse(body).rating !== rating) {
      throw new Error("Une évaluation précédente attend confirmation. Réessayez avec la même réponse.");
    }
    const result = await this.api(`/api/lexicon/entries/${encodeURIComponent(entry.id)}/reviews`, {method: "POST", body});
    this.storage.removeItem(key);
    return result;
  }
  // Suggested ratings after a typed answer: exact match is a recall, anything else a miss.
  static suggest(entry, typed) {
    if (!typed.trim()) return null;
    return normalizeAnswer(typed) === normalizeAnswer(entry.lemma) ? "good" : "again";
  }
}
