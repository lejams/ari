// The exam report (`exam-report-v1`) as lines for the results page. Counts only:
// the API never sends fact ids, and an unknown timing stays visible as unknown.

const RESPONSE_KIND_LABELS = {
  sources: "réponse sur le cas", unknown: "le patient ne sait pas",
  out_of_scope: "question hors du cas", wrong_language: "question dans une autre langue",
};

const plural = (count, singular, pluralForm = `${singular}s`) => `${count} ${count > 1 ? pluralForm : singular}`;

export function formatSeconds(ms) {
  if (!Number.isInteger(ms)) return null;
  return `${(ms / 1000).toFixed(1).replace(".", ",")} s`;
}

export function formatDuration(seconds) {
  if (!Number.isInteger(seconds)) return null;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes ? `${minutes} min${rest ? ` ${String(rest).padStart(2, "0")} s` : ""}` : `${rest} s`;
}

function turnLine(turn) {
  if (!turn.answered) return `Tour ${turn.sequence} · pas de réponse du patient`;
  const parts = [`Tour ${turn.sequence}`, RESPONSE_KIND_LABELS[turn.response_kind] || "réponse"];
  if (turn.audio_failed) parts.push("échec audio");
  else parts.push(turn.heard ? "entendue" : "non entendue (interrompue ou non confirmée)");
  if (turn.facts_selected) parts.push(`faits entendus ${turn.facts_heard}/${turn.facts_selected}`);
  if (turn.free_text === "rejected") parts.push("formulation libre refusée");
  const time = formatSeconds(turn.response_ms);
  if (time) parts.push(time);
  return parts.join(" · ");
}

// Null for a missing report or a schema this page does not know: nothing is guessed.
export function examReportView(report) {
  if (!report || report.schema_version !== "exam-report-v1") return null;
  const answered = report.turns.filter((turn) => turn.answered).length;
  const summary = [];
  const duration = formatDuration(report.duration_seconds);
  if (duration) summary.push(`Durée de l’appel : ${duration}`);
  summary.push(`Réponses du patient entendues : ${report.heard_turns} sur ${answered}${report.not_heard_turns ? ` (${report.not_heard_turns} non entendue${report.not_heard_turns > 1 ? "s" : ""})` : ""}`);
  summary.push(`Faits du cas entendus : ${report.facts_heard}${report.facts_not_heard ? ` · ${plural(report.facts_not_heard, "fait prévu", "faits prévus")} mais non entendu${report.facts_not_heard > 1 ? "s" : ""}` : ""}`);
  const incidents = [
    report.audio_failures ? plural(report.audio_failures, "échec audio", "échecs audio") : "",
    report.transcription_failures ? plural(report.transcription_failures, "transcription en échec", "transcriptions en échec") : "",
    report.free_text_rejections ? plural(report.free_text_rejections, "formulation libre refusée", "formulations libres refusées") : "",
  ].filter(Boolean);
  summary.push(`Incidents techniques : ${incidents.length ? incidents.join(" · ") : "aucun"}`);
  const p50 = formatSeconds(report.response_ms_p50);
  const p95 = formatSeconds(report.response_ms_p95);
  summary.push(p50 && p95
    ? `Temps de réponse du serveur : médiane ${p50} · 95 % des tours sous ${p95}`
    : "Temps de réponse du serveur : non mesuré pour cette session");
  return {
    count: `(${report.heard_turns}/${answered} entendues)`,
    summary,
    turns: report.turns.map(turnLine),
  };
}
