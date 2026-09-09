function ack(type, stream, lastIndex) {
  const value = {type, turn_id: stream.turnId, response_id: stream.responseId ?? null,
    audio_stream_id: stream.audioStreamId, last_index: lastIndex};
  if (type === "audio.playback_started" && stream.speechEndToAudioStartedMs !== undefined)
    value.speech_end_to_audio_started_ms = stream.speechEndToAudioStartedMs;
  if (type === "audio.playback_started" && stream.audioSentToPlaybackStartedMs !== undefined)
    value.audio_sent_to_playback_started_ms = stream.audioSentToPlaybackStartedMs;
  return value;
}

export class PcmPlaybackTracker {
  constructor(emit, now = () => performance.now()) {
    this.emit = emit; this.now = now; this.streams = new Map();
  }
  begin(value) {
    if (!value.turnId || !value.audioStreamId) throw new Error("correlation incomplete");
    const old = this.streams.get(value.turnId); if (old) old.cancelled = true;
    this.streams.set(value.turnId, {...value, received: new Set(), ended: new Set(),
      playbackStarted: new Set(), sent: false, started: false, delivered: false,
      cancelled: false, lastIndex: null, sentAt: null, playbackStartedAt: null});
  }
  attachChunk(value, index, source) {
    const stream = this.#get(value); if (stream.received.has(index)) return;
    stream.received.add(index);
    const ended = () => { if (!stream.cancelled) {
      stream.ended.add(index);
      // An ended source proves playback occurred, but cannot recover its start latency.
      stream.playbackStarted.add(index); this.#flush(stream);
    } };
    if (typeof source.addEventListener === "function") {
      source.addEventListener("ended", ended, {once: true});
    } else {
      source.onended = ended;
    }
  }
  started(value, index) { const stream = this.streams.get(value.turnId);
    if (!stream || stream.cancelled) return;
    if (stream.audioStreamId !== value.audioStreamId ||
        (stream.responseId ?? null) !== (value.responseId ?? null)) throw new Error("correlation mismatch");
    if (!stream.received.has(index)) return;
    stream.playbackStarted.add(index);
    if (stream.playbackStartedAt === null) stream.playbackStartedAt = this.now();
    this.#flush(stream); }
  sent(value, lastIndex) { const stream = this.#get(value); stream.sent = true;
    stream.sentAt = this.now(); stream.lastIndex = lastIndex; this.#flush(stream); }
  cancelAll() { for (const value of this.streams.values()) value.cancelled = true; this.streams.clear(); }
  forget(turnId) { this.streams.delete(turnId); }
  #get(value) { const stream = this.streams.get(value.turnId);
    if (!stream || stream.audioStreamId !== value.audioStreamId ||
        (stream.responseId ?? null) !== (value.responseId ?? null)) throw new Error("correlation mismatch");
    return stream; }
  #flush(stream) {
    if (!stream.sent || stream.cancelled || stream.lastIndex === null) return;
    if (stream.playbackStarted.size && !stream.started) { stream.started = true;
      if (stream.sentAt !== null && stream.playbackStartedAt !== null &&
          stream.playbackStartedAt >= stream.sentAt)
        stream.audioSentToPlaybackStartedMs = Math.max(0,
          Math.round(stream.playbackStartedAt - stream.sentAt));
      this.emit(ack("audio.playback_started", stream, Math.min(...stream.playbackStarted))); }
    const complete = Array.from({length: stream.lastIndex + 1}, (_, i) => i)
      .every((i) => stream.received.has(i) && stream.ended.has(i));
    if (complete && stream.started && !stream.delivered) { stream.delivered = true;
      this.emit(ack("audio.playback_completed", stream, stream.lastIndex)); }
  }
}

export class RealtimePlaybackObserver {
  constructor(emit, now = () => performance.now()) {
    this.emit = emit; this.now = now; this.responses = new Map(); this.active = null;
    this.lastSpeechEndedAt = null;
  }
  speechEnded() { this.lastSpeechEndedAt = this.now(); }
  responseStarted(id) { if (!id) return; this.active = id;
    const speechEndedAt = this.lastSpeechEndedAt; this.lastSpeechEndedAt = null;
    this.responses.set(id, {responseId: id, observed: false, sent: false, cancelled: false,
      speechEndedAt}); }
  mediaAdvanced() { const value = this.responses.get(this.active); if (value && !value.cancelled) {
    if (value.speechEndedAt !== null && value.speechEndToAudioStartedMs === undefined)
      value.speechEndToAudioStartedMs = Math.max(0, Math.round(this.now() - value.speechEndedAt));
    value.observed = true; this.#flush(value); } }
  bindTurn(id, turnId, audioStreamId) { const value = this.responses.get(id) ||
    {responseId: id, observed: false, sent: false, cancelled: false,
      speechEndedAt: this.lastSpeechEndedAt};
    if (!this.responses.has(id)) this.lastSpeechEndedAt = null;
    Object.assign(value, {turnId, audioStreamId}); this.responses.set(id, value); this.#flush(value); }
  cancel(id) { const value = this.responses.get(id); if (value) value.cancelled = true;
    if (this.active === id) this.active = null; }
  cancelAll() { this.responses.clear(); this.active = null; }
  #flush(value) { if (value.observed && !value.sent && !value.cancelled && value.turnId && value.audioStreamId) {
    value.sent = true; this.emit(ack("audio.playback_started", value, 0)); } }
}
