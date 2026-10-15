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

// Place a w x h block horizontally centred inside the allowed area, in one of two bands:
// "center" (the middle of the allowed area) or "lower" (the chest area: below the face
// box, above the bottom safe zone; without a face the lower part of the area); "top"
// (the top of the allowed area, for a stage card that leaves the room below to the
// captions) is a third one, "bottom" (the foot of the area, for landscape captions and
// lower thirds) a fourth. `anchor` moves the block off the horizontal centre: "left",
// "right", "sides" (right edge first, then left; a landscape card beside the picture) or
// "centre+sides" (centred first, the edges only when the centre is taken by the face).
// `position` is the preferred band, then "center" / "lower"; anywhere else (e.g. the strip
// above the head) comes last. When the block would overlap the face box or one of the
// `avoid` boxes (other blocks on screen at the same time), each grown by `margin`, it
// moves just above or below one of them: the spot that clears the face first, then the
// other blocks, then lies in the better band, then closest to that band's own spot.
// `hard` boxes (also in `avoid`: the slide part of a slide_split view) weigh more than
// the face: a block covers the face before it covers one of them.
// `seam` (output y; position "seam"): a caption of a stacked slide_split view sits on the
// boundary between the slide part and the speaker part, centred on it, else just below
// the lowest `hard` box above it (the slide's content box); the face then counts from the
// brow line down (FOREHEAD: hair and forehead may be covered, the eyes and mouth not).
// `inset` keeps that much extra distance from the safe zones (room for transitions that
// draw outside the box). Returns {box, overlapsFace, overlapsBlock, band}.
const SCAN_STEP = 8; // px between candidate positions of the vertical scan
const LOWER_AT = 0.72; // spot of the lower band without a face (share of the allowed height)
const FOREHEAD = 0.25; // top share of a face box above the brow line (a seam caption may cover it)

// The face box from the brow line down.
function faceCore(face) {
  return face ? [face[0], face[1] + (face[3] - face[1]) * FOREHEAD, face[2], face[3]] : null;
}

function placeBlock({ size, safe, w, h, face, avoid = [], hard = [], position = "center", anchor = "center", margin = 24, inset = 0, seam = null }) {
  const [ax0, ay0, ax1, ay1] = allowedArea(size, safe, inset);
  const x0 = Math.round(Math.max(ax0, Math.min(ax1 - w, (size[0] - w) / 2)));
  const clampX = (x) => Math.round(Math.max(ax0, Math.min(ax1 - w, x)));
  // Horizontal spots in order of preference: centred (default), at the left or right edge
  // of the allowed area, or "sides" (right edge, then left; cards beside the picture).
  const xs =
    anchor === "left" ? [clampX(ax0)] : anchor === "right" ? [clampX(ax1)] : anchor === "sides"
          ? [clampX(ax1), clampX(ax0)]
          : anchor === "centre+sides"
            ? [x0, clampX(ax1), clampX(ax0)]
            : [x0];
  const clampY = (y) => Math.round(Math.max(ay0, Math.min(ay1 - h, y)));
  const at = (y, x = xs[0]) => [x, y, x + w, y + h];
  const grow = (b) => [b[0] - margin, b[1] - margin, b[2] + margin, b[3] + margin];
  const onSeam = position === "seam" && Number.isFinite(seam);
  const faceG = face ? grow(onSeam ? faceCore(face) : face) : null;
  const others = avoid.map(grow);
  const hards = hard.map(grow);
  const ah = ay1 - ay0;
  // Each band: whether a block top y lies in it, and its own spot.
  const bands = {
    center: {
      has: (y) => Math.abs(y + h / 2 - (ay0 + ah / 2)) <= ah * 0.2,
      spot: clampY(ay0 + ah / 2 - h / 2),
    },
    lower: faceG
      ? { has: (y) => y >= faceG[3] - 0.5, spot: clampY(Math.max(Math.ceil(faceG[3]), ay0 + ah * LOWER_AT - h / 2)) }
      : { has: (y) => y + h / 2 >= ay0 + ah * 0.5, spot: clampY(ay0 + ah * LOWER_AT - h / 2) },
    top: { has: (y) => y <= ay0 + SCAN_STEP, spot: clampY(ay0) },
    // The foot of the allowed area (landscape captions and lower thirds).
    bottom: { has: (y) => y + h >= ay1 - SCAN_STEP, spot: clampY(ay1 - h) },
  };
  if (onSeam) {
    // Centred on the seam, pushed down below the content box when that reaches lower.
    const above = hards.filter((b) => b[1] < seam).map((b) => Math.ceil(b[3]));
    bands.seam = { has: (y) => y < seam && y + h > seam, spot: clampY(Math.max(seam - h / 2, ...above)) };
  }
  const order = onSeam
    ? ["seam", "lower", "center"]
    : position === "top"
      ? ["top", "center", "lower"]
      : position === "bottom"
        ? ["bottom", "lower", "center"]
        : position === "lower"
          ? ["lower", "center"]
          : ["center", "lower"];
  const rank = (y) => {
    const k = order.findIndex((b) => bands[b].has(y));
    return k < 0 ? order.length : k;
  };
  const ys = order.map((b) => bands[b].spot);
  for (const b of faceG ? [faceG, ...others] : others) ys.push(clampY(Math.ceil(b[3])), clampY(Math.floor(b[1] - h)));
  // Plus a scan of the whole allowed height, so a free gap between several obstacles is
  // found too (the spots next to one obstacle may each collide with another).
  if (faceG || others.length) for (let y = ay0; y <= ay1 - h; y += SCAN_STEP) ys.push(clampY(y));
  const best = ys
    .flatMap((y) =>
      xs.map((x, xr) => {
        const r = rank(y);
        return {
          x,
          y,
          xr,
          rank: r,
          hard: hards.reduce((sum, b) => sum + overlapArea(at(y, x), b), 0),
          face: overlapArea(at(y, x), faceG),
          block: others.reduce((sum, b) => sum + overlapArea(at(y, x), b), 0),
          dist: Math.abs(y - bands[order[Math.min(r, order.length - 1)]].spot),
        };
      }),
    )
    .sort((a, b) => a.hard - b.hard || a.face - b.face || a.block - b.block || a.rank - b.rank || a.xr - b.xr || a.dist - b.dist)[0];
  return {
    box: at(best.y, best.x),
    overlapsFace: best.face > 0,
    overlapsBlock: best.block > 0,
    band: best.rank < order.length ? order[best.rank] : "other",
  };
}

module.exports = { FOREHEAD, breakLines, fitText, faceAt, faceDuring, overlapArea, allowedArea, faceCore, placeBlock };
