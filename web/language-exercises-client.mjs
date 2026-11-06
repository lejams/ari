// Language exercises from the bank, drawn at the learner's working level. Every answer carries an
// idempotency key persisted per profile and exercise, so a lost response or a reload never counts
// an answer twice.
export class LanguageExercisesClient {
  constructor(api, storage = globalThis.sessionStorage, uuid = () => globalThis.crypto.randomUUID()) {
    this.api = api; this.storage = storage; this.uuid = uuid; this.profileId = null;
  }
  key(...parts) { return ["ari:exercises", this.profileId, ...parts].join(":"); }
  overview() { return this.api("/api/language-exercises"); }
  next(category) { return this.api(`/api/language-exercises/next?category=${encodeURIComponent(category)}`); }
  async answer(exercise, chosenIndex) {
    if (!Number.isInteger(chosenIndex)) throw new Error("Choisissez une réponse avant de valider.");
    const key = this.key(exercise.id);
    let requestId = this.storage.getItem(key);
    if (!requestId) { requestId = this.uuid(); this.storage.setItem(key, requestId); }
    const result = await this.api("/api/language-exercises/answers", {
      method: "POST", body: JSON.stringify({ request_id: requestId, exercise_id: exercise.id, chosen_index: chosenIndex }),
    });
    this.storage.removeItem(key);
    return result;
  }
  report(exercise, reason, comment) {
    const text = String(comment || "").trim();
    return this.api(`/api/language-exercises/${encodeURIComponent(exercise.id)}/report`, {
      method: "POST", body: JSON.stringify({ reason, comment: text || null }),
    });
  }
}

export const REPORT_REASONS = [
  { value: "wrong_key", label: "La réponse attendue est fausse" },
  { value: "several_answers", label: "Plusieurs réponses sont justes" },
  { value: "unclear", label: "La phrase est incorrecte ou incompréhensible" },
  { value: "other", label: "Autre chose" },
];

export function verdictText(result) {
  return result.correct ? "Juste." : "Pas tout à fait : voici la réponse attendue.";
}

export function seriesText(results) {
  const total = results.length;
  if (total === 0) return "Aucun exercice fait.";
  const right = results.filter(r => r.correct).length;
  return `${right} réponse${right > 1 ? "s" : ""} juste${right > 1 ? "s" : ""} sur ${total}.`;
}

export function categoryLine(category) {
  const label = category.label[0].toUpperCase() + category.label.slice(1);
  if (!category.available) return `${label} · niveau ${category.level} · rien de neuf pour l’instant`;
  return `${label} · niveau ${category.level} · ${category.available} exercice${category.available > 1 ? "s" : ""}`;
}
