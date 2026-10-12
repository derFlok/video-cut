// Text measuring, line breaking and face/safe-zone aware block placement.
// Boxes are [x0, y0, x1, y1] in output pixels. Everything here is pure: the same
// input gives the same layout, so a block can be placed once and reused per frame.

const { union } = require("./boxes");

// Greedy line breaking over measured word widths. A word wider than `maxWidth`
// gets a line of its own (the caller shrinks the font when that happens).
function breakLines(widths, space, maxWidth) {
  const lines = [];
  let start = 0;
  let width = 0;
  for (let i = 0; i < widths.length; i++) {
    const next = i === start ? widths[i] : width + space + widths[i];
    if (i > start && next > maxWidth) {
      lines.push({ from: start, to: i, width });
      start = i;
      width = widths[i];
    } else {
      width = next;
    }
  }
  if (widths.length) lines.push({ from: start, to: widths.length, width });
  return lines;
}

// Largest font size (from `px` down to `minPx`) at which `texts` fit in `maxWidth`
// on at most `maxLines` lines. `font(px)` returns a CSS font string.
function fitText(ctx, texts, { font, px, minPx = Math.round(px / 2), maxWidth, maxLines = 3 }) {
  let size = Math.round(px);
  for (;;) {
    ctx.font = font(size);
    const widths = texts.map((s) => ctx.measureText(s).width);
    const space = ctx.measureText(" ").width;
    const lines = breakLines(widths, space, maxWidth);
    const fits = lines.length <= maxLines && widths.every((w) => w <= maxWidth);
    if (fits || size <= minPx) {
      const lineHeight = Math.round(size * 1.18);
      return {
        px: size,
        font: font(size),
        widths,
        space,
        lines,
        lineHeight,
        width: Math.max(0, ...lines.map((l) => l.width)),
        height: lines.length * lineHeight,
      };
    }
    size = Math.max(minPx, Math.floor(size * 0.92));
  }
}

// Face box at output time `t`, linearly interpolated between samples; null when
// there are no samples. Before the first / after the last sample the edge sample holds.
function faceAt(samples, t) {
  if (!samples || !samples.length) return null;
  if (t <= samples[0].t) return samples[0].box;
  const last = samples[samples.length - 1];
  if (t >= last.t) return last.box;
  let lo = 0;
  let hi = samples.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (samples[mid].t <= t) lo = mid;
    else hi = mid;
  }
  const a = samples[lo];
  const b = samples[hi];
  const k = b.t > a.t ? (t - a.t) / (b.t - a.t) : 0;
  return a.box.map((v, i) => v + (b.box[i] - v) * k);
}

// Union of all face boxes a block alive from t0 to t1 could meet (samples inside the
// interval plus the neighbours that bound the interpolation at either end).
function faceDuring(samples, t0, t1) {
  if (!samples || !samples.length) return null;
  let box = null;
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i];
    const prev = samples[i - 1];
    const next = samples[i + 1];
    const inside = s.t >= t0 && s.t <= t1;
    const bounds = (next && s.t < t0 && next.t > t0) || (prev && s.t > t1 && prev.t < t1);
    if (inside || bounds) box = union(box, s.box);
  }
  return box || faceAt(samples, t0);
}

function overlapArea(a, b) {
  if (!a || !b) return 0;
  const w = Math.min(a[2], b[2]) - Math.max(a[0], b[0]);
  const h = Math.min(a[3], b[3]) - Math.max(a[1], b[1]);
  return w > 0 && h > 0 ? w * h : 0;
}

// Area a block may use: the frame minus the safe zones, shrunk by `inset` on all sides.
function allowedArea(size, safe, inset = 0) {
  const s = safe || {};
  const side = (s.side || 0) + inset;
  return [side, (s.top || 0) + inset, size[0] - side, size[1] - (s.bottom || 0) - inset];
}

// Place a w x h block horizontally centred inside the allowed area. Vertically it sits
// in the centre band (or the lower part for position "lower"). When it would overlap
// the face box or one of the `avoid` boxes (other blocks on screen at the same time),
// each grown by `margin`, it moves just above or below one of them: the spot that
// clears the face first, then the other blocks, then lies closest to the preferred
// spot. `inset` keeps that much extra distance from the safe zones (room for
// transitions that draw outside the box). Returns {box, overlapsFace, overlapsBlock}.
const SCAN_STEP = 8; // px between candidate positions of the vertical scan

function placeBlock({ size, safe, w, h, face, avoid = [], position = "center", margin = 24, inset = 0 }) {
  const [ax0, ay0, ax1, ay1] = allowedArea(size, safe, inset);
  const x0 = Math.round(Math.max(ax0, Math.min(ax1 - w, (size[0] - w) / 2)));
  const clampY = (y) => Math.round(Math.max(ay0, Math.min(ay1 - h, y)));
  const preferred = clampY(
    position === "lower" ? ay0 + (ay1 - ay0) * 0.72 - h / 2 : (ay0 + ay1) / 2 - h / 2,
  );
  const at = (y) => [x0, y, x0 + w, y + h];
  const grow = (b) => [b[0] - margin, b[1] - margin, b[2] + margin, b[3] + margin];
  const faceG = face ? grow(face) : null;
  const others = avoid.map(grow);
  const ys = [preferred];
  for (const b of faceG ? [faceG, ...others] : others) ys.push(clampY(Math.ceil(b[3])), clampY(Math.floor(b[1] - h)));
  // Plus a scan of the whole allowed height, so a free gap between several obstacles is
  // found too (the spots next to one obstacle may each collide with another).
  if (faceG || others.length) for (let y = ay0; y <= ay1 - h; y += SCAN_STEP) ys.push(clampY(y));
  const best = ys
    .map((y) => ({
      y,
      face: overlapArea(at(y), faceG),
      block: others.reduce((sum, b) => sum + overlapArea(at(y), b), 0),
      dist: Math.abs(y - preferred),
    }))
    .sort((a, b) => a.face - b.face || a.block - b.block || a.dist - b.dist)[0];
  return { box: at(best.y), overlapsFace: best.face > 0, overlapsBlock: best.block > 0 };
}

module.exports = { breakLines, fitText, faceAt, faceDuring, overlapArea, allowedArea, placeBlock };
