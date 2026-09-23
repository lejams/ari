// Fetch boundary of the back-office: same-origin cookie, no cache, JSON unless FormData.
export class BackofficeApi {
  constructor(fetcher = globalThis.fetch.bind(globalThis)) { this.fetcher = fetcher; this.me = null; }
  async api(path, options = {}) {
    const isForm = options.body instanceof FormData;
    const headers = isForm ? {...options.headers} : {"Content-Type": "application/json", ...options.headers};
    const response = await this.fetcher(path, {credentials: "same-origin", cache: "no-store",
      signal: AbortSignal.timeout(isForm ? 120000 : 20000), ...options, headers});
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const detail = payload.detail;
      const message = typeof detail === "string" ? detail
        : Array.isArray(detail) ? detail.map(item => `${(item.loc || []).join(".")} : ${item.msg}`).join(" · ")
        : `Requête refusée (${response.status}).`;
      const error = new Error(message); error.status = response.status; error.detail = detail;
      throw error;
    }
    return response.status === 204 ? null : response.json();
  }
  async restore() {
    try { this.me = await this.api("/api/auth/me"); }
    catch (error) { if (error.status !== 401) throw error; this.me = null; }
    return this.me;
  }
  async login(email, password) { this.me = await this.api("/api/auth/login", {method: "POST", body: JSON.stringify({email, password})}); return this.me; }
  async logout() { await this.api("/api/auth/logout", {method: "POST"}); this.me = null; }
  invitation(token) { return this.api(`/api/auth/invitations/${encodeURIComponent(token)}`); }
  async acceptInvitation(token, password) { this.me = await this.api(`/api/auth/invitations/${encodeURIComponent(token)}/password`, {method: "POST", body: JSON.stringify({password})}); return this.me; }
  has(...roles) { return Boolean(this.me) && roles.some(role => this.me.roles.includes(role)); }
  lands() { return this.api("/api/meta/lands"); }
  dashboard() { return this.api("/api/dashboard"); }
  documents(status = "") { return this.api(`/api/documents${status ? `?status=${encodeURIComponent(status)}` : ""}`); }
  document(id) { return this.api(`/api/documents/${encodeURIComponent(id)}`); }
  pageText(id, number) { return this.api(`/api/documents/${encodeURIComponent(id)}/pages/${number}`); }
  pageImageUrl(id, number) { return `/api/documents/${encodeURIComponent(id)}/pages/${number}/image`; }
  originalUrl(id) { return `/api/documents/${encodeURIComponent(id)}/original`; }
  upload(file, fields) {
    const body = new FormData(); body.append("file", file, file.name);
    for (const [key, value] of Object.entries(fields)) if (value !== null && value !== undefined && value !== "") body.append(key, value);
    return this.api("/api/documents", {method: "POST", body});
  }
  releaseDocument(id) { return this.api(`/api/documents/${encodeURIComponent(id)}/release`, {method: "POST"}); }
  addSegment(id, segment) { return this.api(`/api/documents/${encodeURIComponent(id)}/segments`, {method: "POST", body: JSON.stringify(segment)}); }
  setSegmentStatus(id, status) { return this.api(`/api/segments/${encodeURIComponent(id)}`, {method: "PATCH", body: JSON.stringify({status})}); }
  retryJob(id) { return this.api(`/api/jobs/${encodeURIComponent(id)}/retry`, {method: "POST"}); }
  jobs(status = "") { return this.api(`/api/jobs${status ? `?status=${encodeURIComponent(status)}` : ""}`); }
  protocols(filters = {}) {
    const query = Object.entries(filters).filter(([, value]) => value).map(([key, value]) => `${key}=${encodeURIComponent(value)}`).join("&");
    return this.api(`/api/protocols${query ? `?${query}` : ""}`);
  }
  protocol(id) { return this.api(`/api/protocols/${encodeURIComponent(id)}`); }
  revise(id, baseVersion, baseHash, record) { return this.api(`/api/protocols/${encodeURIComponent(id)}/versions`, {method: "POST", body: JSON.stringify({base_version: baseVersion, base_hash: baseHash, record})}); }
  decide(id, stage, decision) { return this.api(`/api/protocols/${encodeURIComponent(id)}/decisions/${stage}`, {method: "POST", body: JSON.stringify(decision)}); }
  releaseProtocol(id) { return this.api(`/api/protocols/${encodeURIComponent(id)}/release`, {method: "POST"}); }
  deleteProtocol(id) { return this.api(`/api/protocols/${encodeURIComponent(id)}`, {method: "DELETE"}); }
  gold(land = "") { return this.api(`/api/gold${land ? `?land=${encodeURIComponent(land)}` : ""}`); }
  goldDetail(id) { return this.api(`/api/gold/${encodeURIComponent(id)}`); }
  bundleDrafts(goldId) { return this.api(`/api/gold/${encodeURIComponent(goldId)}/bundle-drafts`); }
  requestBundleDraft(goldId, request) { return this.api(`/api/gold/${encodeURIComponent(goldId)}/bundle-drafts`, {method: "POST", body: JSON.stringify(request)}); }
  bundleDraft(id) { return this.api(`/api/bundle-drafts/${encodeURIComponent(id)}`); }
  importBundleDraft(id) { return this.api(`/api/bundle-drafts/${encodeURIComponent(id)}/import`, {method: "POST"}); }
  registryScenarios(status = "") { return this.api(`/api/registry/scenarios${status ? `?status=${encodeURIComponent(status)}` : ""}`); }
  registryScenario(id, version) { return this.api(`/api/registry/scenarios/${encodeURIComponent(id)}/${encodeURIComponent(version)}`); }
  registryReview(id, version, review) { return this.api(`/api/registry/scenarios/${encodeURIComponent(id)}/${encodeURIComponent(version)}/reviews`, {method: "POST", body: JSON.stringify(review)}); }
  registryPublish(id, version) { return this.api(`/api/registry/scenarios/${encodeURIComponent(id)}/${encodeURIComponent(version)}/publish`, {method: "POST"}); }
  registryWithdraw(id, version) { return this.api(`/api/registry/scenarios/${encodeURIComponent(id)}/${encodeURIComponent(version)}/withdraw`, {method: "POST"}); }
  accounts() { return this.api("/api/accounts"); }
  createAccount(account) { return this.api("/api/accounts", {method: "POST", body: JSON.stringify(account)}); }
  deactivate(id) { return this.api(`/api/accounts/${encodeURIComponent(id)}/deactivate`, {method: "POST"}); }
  reinvite(id) { return this.api(`/api/accounts/${encodeURIComponent(id)}/invitations`, {method: "POST"}); }
}
export const STATUS_LABELS = {
  uploaded: "Déposé", text_extracted: "Texte extrait", segmented: "Découpé", extracted: "Extrait", failed: "En échec",
  doctor_review: "À relire", changes_requested: "Corrections demandées", doctor_approved: "À valider", gold: "Gold", rejected: "Rejeté", superseded: "Remplacé",
  pending: "En attente", too_long: "Trop long", discarded: "Écarté", queued: "En file", running: "En cours", succeeded: "Terminé", dead: "Abandonné",
  draft: "Brouillon", invalid: "Invalide", imported: "Importé", draft_unvalidated: "À relire", published: "Publié", withdrawn: "Retiré",
};
export const statusLabel = status => STATUS_LABELS[status] || status;
export const statusTone = status => ({gold: "ok", succeeded: "ok", extracted: "ok", published: "ok", imported: "ok", doctor_approved: "warn", changes_requested: "warn", too_long: "warn", draft: "warn", failed: "bad", dead: "bad", rejected: "bad", invalid: "bad", withdrawn: "bad"}[status] || "");
export const PHASE_LABELS = {arzt_patient: "Arzt–Patient", arzt_arzt: "Arzt–Arzt", fachbegriffe: "Fachbegriffe", arztbrief: "Arztbrief"};
export const PERSONA_LABELS = {standard: "coopératif", anxious: "anxieux", talkative: "bavard", terse: "laconique"};
export const REVIEW_TYPE_LABELS = {clinical: "clinique", linguistic: "linguistique"};
export const ROLE_LABELS = {owner: "Propriétaire", physician_reviewer: "Relecteur médecin", linguistic_reviewer: "Relecteur linguistique"};
export const SECTION_LABELS = {patientendaten: "Patientendaten", aktuelle_beschwerden: "Aktuelle Beschwerden", vorerkrankungen: "Vorerkrankungen", medikamente: "Medikamente", allergien: "Allergien", noxen: "Noxen", familienanamnese: "Familienanamnese", sozialanamnese: "Sozialanamnese", vegetative_anamnese: "Vegetative Anamnese", sonstiges: "Sonstiges"};
export const UNCERTAINTY_PATHS = ["location.land", "location.city", "location.exam_body", "location.exam_date", "location.specialty", "patient.age_years", "patient.sex", "diagnosis.suspected_de", "outcome.result", "arzt_arzt"];
// Mirrors ProtocolRecord.review_blockers so the checklist updates while editing; the server decides.
export function blockers(record, stage) {
  const found = [];
  for (const [holder, name] of [[record.patient, "patient"], [record.diagnosis, "diagnosis"], [record.arzt_arzt, "arzt_arzt"], [record.outcome, "outcome"]]) if (holder?.uncertainty) found.push(`Incertitude ouverte: ${name}: ${holder.uncertainty}`);
  for (const item of record.anamnesis || []) if (item.uncertainty) found.push(`Incertitude ouverte: anamnesis.${item.id}: ${item.uncertainty}`);
  for (const question of record.examiner_questions || []) if (question.uncertainty) found.push(`Incertitude ouverte: examiner_questions.${question.id}: ${question.uncertainty}`);
  for (const uncertainty of record.field_uncertainties || []) if (!uncertainty.resolution) found.push(`Incertitude ouverte: ${uncertainty.path}: ${uncertainty.reason_fr}`);
  for (const question of record.unresolved_questions || []) if (question.critical && !question.answer) found.push(`Question critique sans réponse: ${question.id}`);
  if (!record.pedagogy?.difficulty) found.push("Difficulté non renseignée");
  if (stage === "owner" && !record.location?.land) found.push("Land manquant");
  return found;
}
