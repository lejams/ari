export class PcmResampler {
  constructor(inputRate, outputRate = 24000, batchSize = 960) {
    if (inputRate < outputRate) throw new Error("Upsampling is not supported");
    this.ratio = inputRate / outputRate;
    this.batchSize = batchSize;
    this.source = [];
    this.position = 0;
    this.pending = [];
    this.filtered = 0;
    const cutoffHz = Math.min(9000, outputRate * 0.4);
    this.filterAlpha = 1 - Math.exp((-2 * Math.PI * cutoffHz) / inputRate);
  }

  push(input) {
    for (const sample of input) {
      this.filtered += this.filterAlpha * (sample - this.filtered);
      this.source.push(this.filtered);
    }

    const batches = [];
    while (this.position + 1 < this.source.length) {
      const left = Math.floor(this.position);
      const fraction = this.position - left;
      const sample =
        this.source[left] + (this.source[left + 1] - this.source[left]) * fraction;
      const clipped = Math.max(-1, Math.min(1, sample));
      this.pending.push(clipped < 0 ? clipped * 32768 : clipped * 32767);
      this.position += this.ratio;

      if (this.pending.length === this.batchSize) {
        batches.push(Int16Array.from(this.pending));
        this.pending = [];
      }
    }

    const consumed = Math.floor(this.position);
    if (consumed > 0) {
      this.source = this.source.slice(consumed);
      this.position -= consumed;
    }
    return batches;
  }
}
