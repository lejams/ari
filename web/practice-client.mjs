// Testable request/idempotency boundary. Storage is scoped by the authenticated profile.
export class PracticeClient {
  constructor(fetcher = globalThis.fetch.bind(globalThis), storage = globalThis.sessionStorage,
    uuid = () => globalThis.crypto.randomUUID()) {
    this.fetcher = fetcher; this.storage = storage; this.uuid = uuid; this.profile = null;
  }
  async api(path, options = {}) {
    const response = await this.fetcher(path, {credentials: "same-origin", cache: "no-store",
      signal: AbortSignal.timeout(20000), ...options,
      headers: {"Content-Type": "application/json", ...options.headers}});
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const error = new Error(typeof payload.detail === "string" ? payload.detail : `Requête refusée (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }
  async restoreProfile() {
    try { this.profile = await this.api("/api/profile"); }
    catch (error) { if (error.status !== 404) throw error; this.profile = null; }
    return this.profile;
  }
  async createProfile(target) {
    this.profile = await this.api("/api/learners", {method: "POST", body: JSON.stringify({target_cefr: target})});
    return this.profile;
  }
  async start(content, mode) {
    if (!this.profile) throw new Error("Créez ou retrouvez votre profil avant de commencer.");
    const key = `ari:start:${this.profile.id}:${content.scenario_id}:${content.scenario_version}:${mode}`;
    let requestId = this.storage.getItem(key);
    if (!requestId) { requestId = this.uuid(); this.storage.setItem(key, requestId); }
    const run = await this.api("/api/practice/runs", {method: "POST", body: JSON.stringify({
      scenario_id: content.scenario_id, scenario_version: content.scenario_version, mode, request_id: requestId,
    })});
    this.storage.removeItem(key);
    return run;
  }
  async answer(run, text) {
    if (!text.trim() || text.length > 8000) throw new Error("Saisissez une réponse de 1 à 8000 caractères, pas seulement des espaces.");
    const key = `ari:answer:${this.profile.id}:${run.id}:${run.current_question.id}`;
    let body = this.storage.getItem(key);
    if (!body) {
      body = JSON.stringify({question_id: run.current_question.id, text, event_id: this.uuid()});
      this.storage.setItem(key, body);
    } else if (JSON.parse(body).text !== text) {
      throw new Error("Une réponse précédente attend confirmation. Rechargez l’exercice ou réessayez avec le même texte.");
    }
    const result = await this.api(`/api/practice/runs/${encodeURIComponent(run.id)}/answers`, {method: "POST", body});
    this.storage.removeItem(key);
    return result;
  }
  pendingText(run) {
    if (!run.current_question || !this.profile) return "";
    const saved = this.storage.getItem(`ari:answer:${this.profile.id}:${run.id}:${run.current_question.id}`);
    return saved ? JSON.parse(saved).text : "";
  }
  async getRun(id) {
    const run = await this.api(`/api/practice/runs/${encodeURIComponent(id)}`);
    for (const answer of run.answers) this.storage.removeItem(`ari:answer:${this.profile.id}:${run.id}:${answer.question_id}`);
    return run;
  }
  action(run, name) {
    if (!["pause", "resume", "finish"].includes(name)) throw new Error("Action inconnue");
    return this.api(`/api/practice/runs/${encodeURIComponent(run.id)}/${name}`, {method: "POST"});
  }
}
export const phaseLabel = phase => ({arzt_arzt: "Arzt–Arzt", fachbegriffe: "Fachbegriffe", arzt_patient: "Arzt–Patient"}[phase] || phase);
export const stateLabel = state => ({no_data: "Pas de données", provisional: "Provisoire", evaluated: "Évalué selon cette méthode", active: "En cours", paused: "En pause", completed: "Terminé", observed: "Variante observée", not_matched: "Formulation non reconnue", not_answered: "Sans réponse"}[state] || state);
export const dimensionText = d => `${d.label} : ${d.score === null ? "—" : `${d.score.toFixed(2)} / ${d.max_score}`} · ${stateLabel(d.state)} · ${d.answered_weight === undefined ? d.feedback : `poids répondus ${d.answered_weight} / ${d.expected_weight}`}`;
