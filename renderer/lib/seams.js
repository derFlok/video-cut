// Caption groups cut where a view with a seam (stacked slide_split) starts or ends: a
// group on screen across that cut would sit on the seam in the other layout too (on the
// face there), so each part is placed for its own layout. A word goes to the part its
// start lies in; a part without words is dropped, a part shorter than MIN_PART gives its
// words to the next part (the last one: to the one before) instead of flashing by.
const MIN_PART = 0.4; // s

function splitAtSeams(captions, views) {
  const cuts = [...new Set(views.filter((v) => v.seam != null).flatMap((v) => [v.t0, v.t1]))].sort((a, b) => a - b);
  const out = [];
  for (const c of captions) {
    const inner = cuts.filter((t) => t > c.t0 && t < c.t1);
    if (!inner.length || !Array.isArray(c.words)) {
      out.push(c);
      continue;
    }
    const edges = [c.t0, ...inner, c.t1];
    const parts = [];
    for (let k = 0; k + 1 < edges.length; k++) {
      const [a, b] = [edges[k], edges[k + 1]];
      const last = k + 2 === edges.length;
      const words = c.words.filter((w) => (k === 0 || Number(w.t0) >= a) && (last || Number(w.t0) < b) && String(w.w || "").trim());
      parts.push({ ...c, t0: a, t1: b, words });
    }
    for (let k = 0; k < parts.length && parts.length > 1; k++) {
      const p = parts[k];
      if (p.t1 - p.t0 >= MIN_PART) continue;
      const to = k + 1 < parts.length ? parts[k + 1] : parts[k - 1];
      to.words = k + 1 < parts.length ? [...p.words, ...to.words] : [...to.words, ...p.words];
      parts.splice(k--, 1);
    }
    out.push(...parts.filter((p) => p.words.length));
  }
  return out;
}

module.exports = { splitAtSeams };
