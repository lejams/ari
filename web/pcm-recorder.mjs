// Records the microphone as PCM16 mono 24 kHz through the same worklet as the voice page,
// so the placement speaking task reaches the exact transcription path of a consultation.
export class PcmRecorder {
  constructor(workletUrl = "/pcm-worklet.js") {
    this.workletUrl = workletUrl; this.chunks = []; this.context = null; this.stream = null; this.worklet = null; this.source = null;
  }
  get seconds() { return this.chunks.reduce((total, chunk) => total + chunk.byteLength, 0) / (24000 * 2); }
  async start() {
    this.chunks = [];
    this.context = new AudioContext({ latencyHint: "interactive" });
    if (this.context.state === "suspended") await this.context.resume();
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    await this.context.audioWorklet.addModule(this.workletUrl);
    this.source = this.context.createMediaStreamSource(this.stream);
    this.worklet = new AudioWorkletNode(this.context, "pcm-capture");
    this.worklet.port.onmessage = ({ data }) => { this.chunks.push(new Uint8Array(data)); };
    const mute = this.context.createGain();
    mute.gain.value = 0;
    this.source.connect(this.worklet).connect(mute).connect(this.context.destination);
  }
  async stop() {
    this.stream?.getTracks().forEach((track) => track.stop());
    if (this.worklet) this.worklet.port.onmessage = null;
    await this.context?.close();
    const total = this.chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
    const audio = new Uint8Array(total);
    let offset = 0;
    for (const chunk of this.chunks) { audio.set(chunk, offset); offset += chunk.byteLength; }
    this.context = null; this.stream = null; this.worklet = null; this.source = null;
    return audio;
  }
}
