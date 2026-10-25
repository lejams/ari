import assert from "node:assert/strict";
import {PcmRecorder} from "../pcm-recorder.mjs";

// A start that fails after the microphone opened (the worklet does not load) releases it.
const tracks = [{stopped: false, stop() { this.stopped = true; }}];
let closed = 0;
globalThis.AudioContext = class {
  constructor() { this.state = "running"; this.audioWorklet = {addModule: async () => { throw new Error("worklet unavailable"); }}; }
  async close() { closed += 1; }
};
Object.defineProperty(globalThis, "navigator", {configurable: true, value: {mediaDevices: {getUserMedia: async () => ({getTracks: () => tracks})}}});
const recorder = new PcmRecorder();
await assert.rejects(() => recorder.start(), /worklet unavailable/);
assert.equal(tracks[0].stopped, true, "the microphone is released");
assert.equal(closed, 1, "the audio context is closed");
assert.equal(recorder.stream, null);

// A refused microphone keeps its DOMException name, so the page can explain it.
navigator.mediaDevices.getUserMedia = async () => { throw new DOMException("Permission denied", "NotAllowedError"); };
await assert.rejects(() => new PcmRecorder().start(), {name: "NotAllowedError"});
assert.equal(closed, 2);
console.log("PCM recorder: a failed start releases the microphone and the audio context");
