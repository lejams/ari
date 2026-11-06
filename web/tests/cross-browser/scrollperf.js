// async: scrolls the page top to bottom in 60 steps via rAF, records frame deltas
(async () => {
  const de = document.documentElement; const max = de.scrollHeight - innerHeight; const frames = [];
  if (max <= 0) return {max: 0, frames: 0, long: 0, worst: 0, p95: 0};
  window.scrollTo({top: 0, behavior: 'instant'});
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  let last = performance.now(), n = 0; const steps = 90;
  await new Promise(res => { function f(t) { frames.push(t - last); last = t; n++; window.scrollTo({top: max * n / steps, behavior: 'instant'}); if (n < steps) requestAnimationFrame(f); else res(); } requestAnimationFrame(f); });
  window.scrollTo({top: 0, behavior: 'instant'});
  frames.shift(); frames.sort((a, b) => a - b);
  return {max, frames: frames.length, long: frames.filter(x => x > 24).length, worst: Math.round(frames[frames.length - 1]), p95: Math.round(frames[Math.floor(frames.length * .95)])};
})()
