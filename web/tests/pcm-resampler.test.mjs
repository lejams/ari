import assert from "node:assert/strict";

import { PcmResampler } from "../pcm-resampler.mjs";

for (const inputRate of [44100, 48000]) {
  const resampler = new PcmResampler(inputRate, 24000, 960);
  const batches = [];
  for (let offset = 0; offset < inputRate; offset += 128) {
    const length = Math.min(128, inputRate - offset);
    const input = new Float32Array(length);
    for (let index = 0; index < length; index += 1) {
      input[index] = Math.sin((2 * Math.PI * 440 * (offset + index)) / inputRate);
    }
    batches.push(...resampler.push(input));
  }

  assert.ok(batches.length >= 24 && batches.length <= 25);
  assert.ok(batches.every((batch) => batch.length === 960));
  assert.ok(batches.length <= 25, "audio should be sent in roughly 40 ms batches");
}
