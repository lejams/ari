// Evaluated in the page. Returns plain JSON. Same code runs in Chromium and WebKit.
(() => {
  const vw = innerWidth, vh = innerHeight;
  const touch = matchMedia('(pointer:coarse)').matches || vw <= 1024;
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const sel = el => el.id ? '#' + el.id : (el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).slice(0,2).join('.') : ''));
  const inScroller = el => { for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
    const o = getComputedStyle(p); if (/(auto|scroll|hidden|clip)/.test(o.overflowX) && p.scrollWidth > p.clientWidth + 1 || /(auto|scroll)/.test(o.overflowX)) return true; } return false; };
  const out = {vw, vh, dpr: devicePixelRatio, ua: navigator.userAgent.slice(0, 80)};
  const de = document.documentElement;
  out.hOverflow = de.scrollWidth - vw;
  out.docHeight = de.scrollHeight;
  // elements sticking out of the viewport horizontally (not in a scroll container)
  const off = [];
  for (const el of document.body.querySelectorAll('*')) {
    if (!vis(el)) continue; const r = el.getBoundingClientRect(); const pos = getComputedStyle(el).position;
    if (el.classList.contains('skip') || (pos === 'fixed' && (r.right <= vw + 1))) continue;
    if ((r.right > vw + 1 || r.left < -1) && !inScroller(el) && !el.closest('svg') && !el.closest('.sec-aura,.pulse-bg,.hero-grid-lines')) off.push(sel(el) + ' [' + Math.round(r.left) + ',' + Math.round(r.right) + ']');
    if (off.length > 8) break;
  }
  out.offscreen = off;
  // touch targets
  const min = touch ? 44 : 24, small = [];
  for (const el of document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=tab]')) {
    if (!vis(el) || el.closest('.sr-only') || el.classList.contains('skip')) continue; const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    let w = r.width, h = r.height;
    if (el.tagName === 'A' && getComputedStyle(el).display === 'inline') { // inline text links are exempt (WCAG 2.5.8)
      continue; }
    if (el.type === 'radio' || el.type === 'checkbox') { const l = el.closest('label'); if (l) { const lr = l.getBoundingClientRect(); w = lr.width; h = lr.height; } }
    const dense = el.closest('.bottomnav,.week-strip,.lang-switch'); const minW = dense && touch ? (vw <= 340 ? 34 : 40) : min;
    if (w < minW - 0.5 || h < min - 0.5) small.push(sel(el) + ' ' + Math.round(w) + '×' + Math.round(h));
  }
  out.smallTargets = small.slice(0, 12); out.smallTargetCount = small.length; out.targetMin = min;
  // fields that make iOS zoom in on focus (< 16px)
  const zoom = [];
  if (touch) for (const el of document.querySelectorAll('input:not([type=hidden]):not([type=radio]):not([type=checkbox]),select,textarea')) {
    if (!vis(el)) continue; if (parseFloat(getComputedStyle(el).fontSize) < 16) zoom.push(sel(el) + ' ' + getComputedStyle(el).fontSize); }
  out.iosZoomFields = zoom;
  // truncated labels
  const trunc = [];
  for (const el of document.querySelectorAll('body *')) { if (!vis(el)) continue; const s = getComputedStyle(el);
    if (s.textOverflow === 'ellipsis' && el.scrollWidth > el.clientWidth + 1) trunc.push(sel(el) + ' "' + (el.textContent||'').trim().slice(0, 24) + '"'); }
  out.truncated = trunc.slice(0, 10);
  // tiny text
  const tiny = new Set();
  for (const el of document.querySelectorAll('body *')) { if (!vis(el) || !el.childNodes.length || el.closest('.bottomnav') || el.closest('.sr-only')) continue;
    const t = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 1); if (!t) continue;
    const fs = parseFloat(getComputedStyle(el).fontSize); if (fs < (touch ? 10.9 : 10.4)) tiny.add(sel(el) + ' ' + fs); }
  out.tinyText = [...tiny].slice(0, 8);
  // fixed bars
  const fixed = [];
  for (const el of document.querySelectorAll('body *')) { const s = getComputedStyle(el);
    if ((s.position === 'fixed' || s.position === 'sticky') && vis(el)) { const r = el.getBoundingClientRect();
      if (r.height > 0 && r.width > vw * .5 || r.height > 0 && s.position==='fixed') fixed.push({s: sel(el), top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height), pos: s.position}); } }
  out.fixed = fixed.slice(0, 6);
  out.fontsLoaded = document.fonts ? [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family).slice(0, 4) : [];
  out.bodyFont = getComputedStyle(document.body).fontFamily.slice(0, 40);
  out.scrollBehavior = getComputedStyle(de).scrollBehavior;
  out.scrollPaddingTop = getComputedStyle(de).scrollPaddingTop;
  out.scrollPaddingBottom = getComputedStyle(de).scrollPaddingBottom;
  out.overscroll = getComputedStyle(document.body).overscrollBehaviorY;
  return out;
})()
