import { PcmResampler } from "./pcm-resampler.mjs";

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.resampler = new PcmResampler(sampleRate, 24000, 960);
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (const pcm of this.resampler.push(input)) {
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
