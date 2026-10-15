// Shared pieces of the slide templates (renderer/slides/*.js): an opaque full-clip slide
// on the brand background, laid out once inside the content area the core gives
// (`slide.area`, clip px: the room left beside the speaker bubble / the speaker part),
// at the largest scale from 1 down to MIN_SCALE that fits, and built step by step
// ("reveal" times in clip seconds, else a calm automatic pace).
// Colours, fonts and motion come only from env.tokens (via lib/style.js and lib/card.js).

const style = require("./style");
const card = require("./card");

// Layout scales tried in order: larger than the base sizes when the slide has room (a
// short slide reads better big on a phone), never below 0.6 of them.
const SCALES = [1.3, 1.15, 1, 0.9, 0.8, 0.7, 0.6];
const MIN_SCALE = SCALES[SCALES.length - 1];
const AUTO_FIRST = 0.5; // s: the first build step without reveal times
const AUTO_STEP = 0.7; // s between automatic build steps
const CALM = 0.7; // entrances run on the brand spring slowed to this rate
const LINE = 1.25; // line height of slide text (a little airier than a caption)

// WCAG contrast ratio of two hex colours (null when one is not a hex colour).
function contrast(a, b) {
  const la = card.luminance(a);
  const lb = card.luminance(b);
  if (la == null || lb == null) return null;
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

// Colour roles on the slide: the plate (colors.bg) and its ink (colors.text), a muted ink
// for secondary text when it still reads (>= 4.5:1, else the ink), the accent for marks
// and for large text only where it reaches 3:1 (else the ink), the card surface.
function palette(tokens) {
  const { plate, ink } = card.plateColors(tokens, false);
  const raw = tokens.colors || {};
  const ok = (c, min) => c && (contrast(c, plate) ?? 0) >= min;
  const accent = raw.accent || ink;
  return {
    plate,
    ink,
    muted: ok(raw.muted, 4.5) ? raw.muted : ink,
    accent,
    accentText: ok(accent, 3) ? accent : ink,
    surface: raw.surface && raw.surface !== plate ? raw.surface : null,
    sparing: card.palette(tokens).sparing,
  };
}

// Sizes from the short side of the output frame (env.unit), so a slide reads the same in
// every format; `k` is the layout scale.
function sizes(env, k) {
  const u = env.unit * k;
  return { display: u * 0.085, heading: u * 0.062, body: u * 0.046, label: u * 0.036, big: u * 0.2, gap: u * 0.03 };
}

// A text run at exactly `px` (no hidden shrinking: the layout scale is the only one), in
// at most `maxLines` lines of `maxWidth`. `over` is true when it does not fit.
function run(ctx, env, text, { px, role = "body", maxWidth, maxLines = 3 }) {
  const p = Math.max(1, Math.round(px));
  const r = card.textRun(ctx, env, text, { px: p, role, maxWidth, maxLines, minPx: p });
  const lineHeight = Math.round(r.px * LINE);
  return { ...r, lineHeight, height: r.rows.length * lineHeight, over: r.rows.length > maxLines || r.width > maxWidth + 0.5 };
}

// Lay out with `measure(k)` -> {w, h, over, ...} at the largest scale that fits the area
// (w, h within it and no run over its lines). Returns the measure plus {k, fits, area}.
function fit(area, measure) {
  const aw = area[2] - area[0];
  const ah = area[3] - area[1];
  let L = null;
  for (const k of SCALES) {
    L = measure(k, aw, ah);
    L.k = k;
    L.fits = !L.over && L.w <= aw + 0.5 && L.h <= ah + 0.5;
    if (L.fits) break;
  }
  L.area = area;
  // Top-left of a w x h block centred in the area.
  L.x = Math.round(area[0] + Math.max(0, (aw - L.w) / 2));
  L.y = Math.round(area[1] + Math.max(0, (ah - L.h) / 2));
  return L;
}

// Start times (clip seconds) of `n` build steps: the reveal times given (a missing one
// follows the one before at the automatic pace), else AUTO_FIRST + k * AUTO_STEP.
function stepTimes(n, reveal) {
  const given = (Array.isArray(reveal) ? reveal : []).filter(Number.isFinite);
  const out = [];
  for (let k = 0; k < n; k++) {
    if (k < given.length) out.push(Math.max(0, given[k]));
    else out.push(k === 0 ? AUTO_FIRST : out[k - 1] + AUTO_STEP);
  }
  return out;
}

// Monotone entrance progress 0..1 `t` seconds after an element's start (the brand spring,
// slowed for calm; a "cut" brand shows it at once).
function enterP(tokens, t) {
  if (!(t >= 0)) return 0;
  return card.settle(tokens, t * CALM);
}

// Seconds an entrance takes (until enterP reaches 1).
function enterTime(tokens) {
  return style.transition(tokens) === "cut" ? 0 : card.reachTime(tokens) / CALM;
}

// Draw `paint(ctx)` with the brand entrance at progress p around `box`; returns the box
// as drawn, or null when it is not visible yet.
function enter(ctx, env, p, px, box, paint) {
  if (!(p > 0)) return null;
  ctx.save();
  const drawn = style.enter(ctx, env.tokens, { p, move: p }, px, box);
  paint(ctx);
  ctx.restore();
  return drawn;
}

// A text run's box with its top-left (align left), top-centre (center) or top-right at x, y.
function runBox(r, x, y, align = "left") {
  const x0 = align === "center" ? x - r.width / 2 : align === "right" ? x - r.width : x;
  return [x0, y, x0 + r.width, y + r.height];
}

function drawRun(ctx, env, r, x, y, align, color) {
  card.drawRun(ctx, env, r, x, y, align, color);
}

// Text the core checks (reading time, bubble over text): every run with its final box.
function textEntry(r, box) {
  return { text: r.rows.map((row) => row.text).join(" "), box: box.map((v) => Math.round(v)) };
}

function words(texts) {
  return texts.reduce((n, t) => n + t.text.split(/\s+/).filter(Boolean).length, 0);
}

// Fill the whole clip with the plate.
function plate(ctx, env) {
  ctx.save();
  ctx.fillStyle = palette(env.tokens).plate;
  ctx.fillRect(0, 0, env.width, env.height);
  ctx.restore();
}

// A filled circle (markers, timeline points).
function dot(ctx, x, y, r, color) {
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

// A line from (x0, y0) towards (x1, y1), drawn to share `k` (0..1) of its length.
function line(ctx, x0, y0, x1, y1, width, color, k = 1) {
  if (!(k > 0)) return;
  ctx.beginPath();
  ctx.moveTo(x0, y0);
  ctx.lineTo(x0 + (x1 - x0) * Math.min(1, k), y0 + (y1 - y0) * Math.min(1, k));
  ctx.lineWidth = width;
  ctx.lineCap = "round";
  ctx.strokeStyle = color;
  ctx.stroke();
}

// An arrow head pointing from (x0, y0) to (x1, y1) at the far end.
function arrow(ctx, x0, y0, x1, y1, width, color, k = 1) {
  if (!(k > 0)) return;
  line(ctx, x0, y0, x1, y1, width, color, k);
  if (k < 1) return;
  const a = Math.atan2(y1 - y0, x1 - x0);
  const s = width * 3.2;
  ctx.beginPath();
  ctx.moveTo(x1 + Math.cos(a + 2.5) * s, y1 + Math.sin(a + 2.5) * s);
  ctx.lineTo(x1, y1);
  ctx.lineTo(x1 + Math.cos(a - 2.5) * s, y1 + Math.sin(a - 2.5) * s);
  ctx.lineWidth = width;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.strokeStyle = color;
  ctx.stroke();
}

function roundRect(ctx, box, r, color) {
  ctx.beginPath();
  ctx.roundRect(box[0], box[1], box[2] - box[0], box[3] - box[1], r);
  ctx.fillStyle = color;
  ctx.fill();
}

// Typographic quotes of the job language around a text.
function quoted(text, lang) {
  return lang === "en" ? `“${text}”` : `„${text}“`;
}

// A number for display in the job language: German groups with "." and has a decimal
// comma; English the other way round.
function formatValue(v, lang) {
  const s = Number.isInteger(v) ? String(v) : String(Math.round(v * 100) / 100);
  const [i, f] = s.split(".");
  const sep = lang === "en" ? [",", "."] : [".", ","];
  const int = i.replace(/\B(?=(\d{3})+(?!\d))/g, sep[0]);
  return f ? `${int}${sep[1]}${f}` : int;
}

module.exports = {
  SCALES,
  MIN_SCALE,
  LINE,
  contrast,
  palette,
  sizes,
  run,
  fit,
  stepTimes,
  enterP,
  enterTime,
  enter,
  runBox,
  drawRun,
  textEntry,
  words,
  plate,
  dot,
  line,
  arrow,
  roundRect,
  quoted,
  formatValue,
};
