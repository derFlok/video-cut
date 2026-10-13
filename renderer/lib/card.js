// Shared pieces of the card scenes (number_card, list_item, chat_bubble, ...): brand
// colour roles, font weight per role, wrapped text runs, a monotone "settle" curve
// from the brand spring, and face/safe-zone aware placement of a measured card.
// Colours, fonts and motion come only from env.tokens (via lib/style.js).

const style = require("./style");
const { step, brandSpring } = require("./spring");
const { fitText, placeBlock, allowedArea } = require("./layout");

// Relative luminance (WCAG) of a #rgb / #rrggbb token; null when it is not a hex colour.
function luminance(hex) {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex || "").trim());
  if (!m) return null;
  const h = m[1].length === 3 ? m[1].replace(/./g, (c) => c + c) : m[1];
  const lin = (i) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(0) + 0.7152 * lin(2) + 0.0722 * lin(4);
}

// Colour roles of a card. `fill`/`text`/`accent` are the caption colours (contrast
// checked by the brand loader); `sparing` is true unless the brand allows the accent
// as a large fill (colors.accent_usage).
function palette(tokens) {
  const c = style.captionColors(tokens);
  const raw = tokens.colors || {};
  return {
    fill: c.box,
    text: c.text,
    accent: c.highlight,
    muted: raw.muted || c.text,
    sparing: !/frei|free/i.test(String(raw.accent_usage || "")),
  };
}

// The full-frame plate of an end card and the ink on it: colors.bg with colors.text,
// or, for `dark`, whichever of the two is darker as the plate.
function plateColors(tokens, dark) {
  const raw = tokens.colors || {};
  let plate = raw.bg || style.captionColors(tokens).box;
  let ink = raw.text || style.captionColors(tokens).text;
  if (dark && (luminance(ink) ?? 1) < (luminance(plate) ?? 0)) [plate, ink] = [ink, plate];
  return { plate, ink };
}

// Font weight for a text role: display and heading take the strongest brand weight,
// body the one closest to regular, label the one in between.
function weightFor(tokens, role) {
  const ws = style.weights(tokens).slice().sort((a, b) => a - b);
  if (role === "body") return ws.reduce((a, b) => (Math.abs(b - 400) < Math.abs(a - 400) ? b : a));
  if (role === "label") return ws[Math.floor((ws.length - 1) / 2)];
  return ws[ws.length - 1];
}

// Time (s) at which the brand spring first reaches its target: the first crossing for
// an underdamped spring, 99.9 % for a (critically or over) damped one.
function reachTime(tokens) {
  const { w, z } = brandSpring(tokens);
  if (!(w > 0)) return 0;
  if (z < 1) {
    const wd = w * Math.sqrt(1 - z * z);
    return (Math.PI - Math.atan(wd / (z * w))) / wd;
  }
  let lo = 0;
  let hi = 60 / w;
  for (let i = 0; i < 40; i++) {
    const mid = (lo + hi) / 2;
    if (step(mid, w, z) >= 0.999) hi = mid;
    else lo = mid;
  }
  return hi;
}

// Monotone 0..1 progress `tau` seconds after a start: the brand spring up to the
// moment it first reaches its target, 1 after (no overshoot, no dip). For counters,
// bars, zooms and plates, where a value must never run backwards.
function settle(tokens, tau) {
  if (!(tau > 0)) return 0;
  if (style.transition(tokens) === "cut") return 1;
  if (tau >= reachTime(tokens)) return 1;
  const { w, z } = brandSpring(tokens);
  return Math.max(0, Math.min(1, step(tau, w, z)));
}

// Words of `text` laid out at up to `px` (shrinking to `minPx`) in at most `maxLines`
// lines of `maxWidth`. Returns {font, px, lineHeight, rows: [{text, width}], width, height}.
function textRun(ctx, env, text, { px, role = "body", maxWidth, maxLines = 3, minPx }) {
  const { tokens } = env;
  const words = String(text || "").trim().split(/\s+/).filter(Boolean);
  const weight = weightFor(tokens, role);
  ctx.save();
  const fit = fitText(ctx, words.length ? words : [""], {
    font: (p) => {
      ctx.letterSpacing = style.tracking(tokens, role === "display" ? "display" : role === "body" ? "body" : "heading", p);
      return style.font(env, p, weight);
    },
    px,
    minPx: minPx || Math.round(px * 0.6),
    maxWidth,
    maxLines,
  });
  ctx.restore();
  const rows = fit.lines.map((l) => ({
    text: words.slice(l.from, l.to).join(" "),
    width: l.width,
  }));
  return { font: fit.font, px: fit.px, role, lineHeight: fit.lineHeight, rows, width: fit.width, height: fit.height };
}

// Draw a text run with its top-left (align "left"), top-centre ("center") or
// top-right ("right") at x, y.
function drawRun(ctx, env, run, x, y, align, color) {
  const role = run.role === "display" ? "display" : run.role === "body" ? "body" : "heading";
  ctx.save();
  ctx.font = run.font;
  ctx.letterSpacing = style.tracking(env.tokens, role, run.px);
  ctx.textBaseline = "middle";
  ctx.textAlign = align;
  ctx.fillStyle = color;
  run.rows.forEach((r, i) => ctx.fillText(r.text, x, y + (i + 0.5) * run.lineHeight));
  ctx.restore();
}

// Content width a card may use at most (safe area minus transition bleed and padding).
// In a landscape frame a card stays in a side column (at most 33 % of the frame width,
// 50 % for a `wide` card such as a comparison).
function maxCardWidth(env, px, pad, wide = false) {
  const side = (env.safe && env.safe.side) || 0;
  const f = style.frame(env);
  const room = f.w - 2 * side - 2 * style.bleed(px) - 2 * pad;
  if (f.tall) return room;
  const share = f.landscape ? (wide ? 0.5 : 0.33) : wide ? 0.88 : 0.66;
  return Math.min(room, f.w * share - 2 * pad) * (env.cardNarrow || 1);
}

// Place a card measured by `measure(k)` (k: scale 1 .. 0.6; returns {w, h, px, ...}),
// clear of the face during the item's life, of blocks already placed, and inside the
// safe area with room for the transition. Shrinks before it gives up; registers the
// block so later items avoid it. Returns the measure plus {box, reach, fits,
// overlapsFace, overlapsBlock}. `stage`: the card is the picture of the moment and may
// cover the speaker (a portrait screenshot): placed clear of other blocks only, at the
// largest size that fits.
// A frame that is not tall keeps above the strip a bottom caption needs. In a landscape
// frame a card also goes to the free side (right edge first, then left); `landscape: {position, anchor,
// reserve}` overrides that per scene (a lower third sits at the foot, bottom left).
function layoutCard(env, item, measure, { position = "center", stage = false, landscape } = {}) {
  const f = style.frame(env);
  // A frame that is not tall (square, 4:5) keeps the centre first and uses the sides
  // when the face leaves no room there; 9:16 stays centred as before.
  let anchor = f.tall ? "center" : "centre+sides";
  let safe = env.safe;
  if (!f.tall) {
    const o = f.landscape
      ? { position: "center", anchor: "sides", reserve: style.captionReserve(env), ...landscape }
      : { position, anchor, reserve: style.captionReserve(env) };
    ({ position, anchor } = o);
    safe = { ...(env.safe || {}), bottom: ((env.safe && env.safe.bottom) || 0) + o.reserve };
  }
  const { size } = env;
  let L = null;
  // A frame that is not tall tries a narrower card (tall and taller) before a smaller
  // one, so a card can stand beside the face; 9:16 only ever shrinks.
  const attempts = [];
  for (const k of [1, 0.85, 0.72, 0.6]) for (const narrow of f.tall || stage ? [1] : [1, 0.75, 0.55]) attempts.push([k, narrow]);
  for (const [k, narrow] of attempts) {
    env.cardNarrow = narrow;
    const m = measure(k);
    env.cardNarrow = 1;
    const reach = style.bleed(m.px);
    const placed = placeBlock({
      size,
      safe,
      w: Math.ceil(m.w),
      h: Math.ceil(m.h),
      face: stage ? null : env.faceDuring(item.t0, item.t1),
      avoid: env.blocksDuring(item.t0, item.t1),
      position,
      anchor,
      margin: reach,
      inset: reach,
    });
    const [ax0, ay0, ax1, ay1] = allowedArea(size, safe, reach);
    const fits = m.w <= ax1 - ax0 && m.h <= ay1 - ay0;
    L = { ...m, ...placed, reach, fits, stage };
    // A frame that is not tall has less room to spare: shrink until the card also clears
    // the blocks already placed (9:16 keeps its older rule).
    const crowded = L.overlapsBlock && (stage || !f.tall);
    if (!L.overlapsFace && !crowded && fits) break;
  }
  const m = L.reach;
  env.addBlock(item.t0, item.t1, [L.box[0] - m, L.box[1] - m, L.box[2] + m, L.box[3] + m]);
  return L;
}

// Draw a placed card `t` seconds into a life of `dur` seconds: the brand transition,
// the card plate (unless `plate: false`), then `paint(ctx, box, a)` for the content.
// Returns the box actually drawn, or null when nothing is visible.
function drawCard(ctx, env, L, t, dur, paint, { plate = true, color } = {}) {
  const { tokens } = env;
  const a = style.appear(tokens, t, dur);
  if (a.p <= 0) return null;
  ctx.save();
  const drawn = style.enter(ctx, tokens, a, L.px, L.box);
  if (plate) {
    ctx.fillStyle = color || palette(tokens).fill;
    ctx.beginPath();
    ctx.roundRect(L.box[0], L.box[1], L.box[2] - L.box[0], L.box[3] - L.box[1], style.radius(tokens, L.px));
    ctx.fill();
  }
  paint(ctx, L.box, a);
  ctx.restore();
  return drawn;
}

module.exports = {
  luminance,
  palette,
  plateColors,
  weightFor,
  reachTime,
  settle,
  textRun,
  drawRun,
  maxCardWidth,
  layoutCard,
  drawCard,
};
