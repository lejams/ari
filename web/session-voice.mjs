export function persistedVoiceSelection(session) {
  if (!session?.voice_stack_id || !["pipeline", "realtime"].includes(session.voice_transport))
    throw new Error("Stack vocale persistée invalide");
  if (session.voice_stack_available !== true)
    throw new Error("La stack vocale enregistrée n'est plus disponible");
  return {stackId: session.voice_stack_id, transport: session.voice_transport};
}
export function acceptedFallbackRequest(offer, accepted) {
  if (!accepted) throw new Error("Le fallback requiert un accord explicite");
  if (!offer?.available) throw new Error("Fallback indisponible");
  return {failed_voice_stack_id: offer.failed_voice_stack_id,
    target_voice_stack_id: offer.target_voice_stack_id};
}
