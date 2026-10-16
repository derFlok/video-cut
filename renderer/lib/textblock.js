// A block of words laid out once and drawn per frame in the brand caption style
// (box | outline | plain). Shared by the caption and keyword scenes.

const style = require("./style");
const { fitText, placeBlock, allowedArea } = require("./layout");

// Lay out `texts` (already cased) starting at font size `px`, placed in the preferred
// band (`position`, else tokens.captions.position: center | lower) outside the safe zones, away from the face during [t0, t1] and from blocks
// laid out before it that share that time. When the face cannot be cleared or the
// block does not fit the safe area, it shrinks (down to 60 %) before it gives up; a block
// that only clears the face outside both bands (e.g. above the head) shrinks too, and the
// largest size that lands in a band wins. The renderer lays out all items
// once, in time order, before the first frame.
// How much the active word grows with highlight "scale" (layout reserves this room).
const SCALE_GROWTH = 0.12;

// A scaled word pushes the other words of its line aside (half its growth each way),
// so it never runs into its neighbours; the line stays centred. Returns the x shift
// per word and the most any line grew on one side.
function reflow(words, states) {
  const dx = words.map(() => 0);
  let spread = 0;
  states.forEach((s, i) => {
    if (!(s.scale > 0)) return;
    const g = (SCALE_GROWTH * s.scale * words[i].width) / 2;
    words.forEach((w, j) => {
      if (w.y === words[i].y && j !== i) dx[j] += j < i ? -g : g;
    });
    spread = Math.max(spread, g);
  });
  return { dx, spread };
}

function layoutBlock(ctx, env, texts, { px, role = "heading", maxLines = 3, position, anchor, share, reserve = 0, t0, t1 }) {
  let L = null;
  let clear = null; // the largest attempt clear of the face, in no band
  for (const k of [1, 0.85, 0.72, 0.6]) {
    L = attempt(ctx, env, texts, { px: px * k, role, maxLines, position, anchor, share, reserve, t0, t1 });
    if (!L.overlapsFace && L.fits && L.band !== "other") break;
    if (!L.overlapsFace && L.fits) clear = clear || L;
  }
  if (L.overlapsFace || !L.fits || L.band === "other") L = clear || L;
  const m = L.reach;
  env.addBlock(t0, t1, [L.box[0] - m, L.box[1] - m, L.box[2] + m, L.box[3] + m]);
  return L;
}

function attempt(ctx, env, texts, { px, role, maxLines, position, anchor, share, reserve, t0, t1 }) {
  const { tokens, size } = env;
  // `reserve`: extra room kept free at the foot (the strip of a bottom caption).
  const safe = reserve ? { ...(env.safe || {}), bottom: ((env.safe && env.safe.bottom) || 0) + reserve } : env.safe;
  const cs = style.captionSettings(tokens);
  const bleed = style.bleed(px);
  const padX = cs.style === "box" ? Math.round(px * 0.45) : Math.round(px * 0.25);
  const padY = Math.round(px * 0.25);
  const scaleRoom = cs.highlight.scale ? SCALE_GROWTH : 0;
  const f = style.frame(env);
  // A landscape frame keeps text lines to 70 % of its width (`share`: another share of
  // the frame width for the whole block, e.g. a side column).
  const cap = share ? f.w * share : f.landscape ? f.w * 0.7 : Infinity;
  const avail = Math.min(size[0] - 2 * ((safe && safe.side) || 0) - 2 * bleed - 2 * padX, cap - 2 * padX);
  ctx.save();
  const fit = fitText(ctx, texts, {
    font: (p) => {
      ctx.letterSpacing = style.tracking(tokens, role, p);
      return style.font(env, p);
    },
    px,
    maxWidth: avail / (1 + scaleRoom),
    maxLines,
  });
  ctx.restore();
  const w = Math.ceil(fit.width + 2 * padX);
  const h = Math.ceil(fit.height + 2 * padY);
  // A scaled active word may grow out of the block by half its growth on each side.
  const grow = Math.ceil((scaleRoom / 2) * Math.max(fit.lineHeight, ...fit.widths));
  // How far drawing can reach outside the box (transition bleed + word growth).
  const reach = bleed + grow;
  const { box, overlapsFace, overlapsBlock, band } = placeBlock({
    size,
    safe,
    w,
    h,
    face: env.faceDuring(t0, t1),
    avoid: env.blocksDuring(t0, t1),
    hard: env.hardDuring ? env.hardDuring(t0, t1) : [],
    position: position || cs.position,
    anchor,
    margin: reach,
    inset: reach,
  });
  const [ax0, ay0, ax1, ay1] = allowedArea(size, safe, reach);
  const fits = w <= ax1 - ax0 && h <= ay1 - ay0;
  const words = [];
  fit.lines.forEach((line, li) => {
    let x = box[0] + (w - line.width) / 2;
    const y = box[1] + padY + (li + 0.5) * fit.lineHeight;
    for (let i = line.from; i < line.to; i++) {
      words.push({ text: texts[i], x, y, width: fit.widths[i] });
      x += fit.widths[i] + fit.space;
    }
  });
  return { ...fit, style: cs.style, role, box, band, grow, reach, fits, overlapsFace, overlapsBlock, words };
}

// Draw a laid-out block `t` seconds into a life of `dur` seconds. `wordState(i)`
// returns {highlight: bool, underline: 0..1, scale: 0..1} for word i.
// Returns the bounding box actually drawn, or null when nothing was visible.
function drawBlock(ctx, env, L, t, dur, wordState) {
  const { tokens } = env;
  const a = style.appear(tokens, t, dur);
  if (a.p <= 0) return null;
  const colors = style.captionColors(tokens);
  const states = L.words.map((_, i) => wordState(i));
  const { dx, spread } = reflow(L.words, states);
  ctx.save();
  const drawn = style.enter(ctx, tokens, a, L.px, L.box);
  if (L.style === "box") {
    ctx.fillStyle = colors.box;
    ctx.beginPath();
    const [x0, y0, x1, y1] = [L.box[0] - spread, L.box[1], L.box[2] + spread, L.box[3]];
    ctx.roundRect(x0, y0, x1 - x0, y1 - y0, style.radius(tokens, L.px));
    ctx.fill();
  }
  ctx.font = L.font;
  ctx.letterSpacing = style.tracking(tokens, L.role, L.px);
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";
  ctx.lineJoin = "round";
  L.words.forEach((w, i) => {
    const s = states[i];
    const word = { ...w, x: w.x + dx[i] };
    ctx.save();
    if (s.scale > 0) {
      const k = 1 + SCALE_GROWTH * s.scale;
      const cx = word.x + word.width / 2;
      ctx.translate(cx, word.y);
      ctx.scale(k, k);
      ctx.translate(-cx, -word.y);
    }
    if (L.style === "outline") {
      ctx.strokeStyle = colors.box;
      ctx.lineWidth = L.px * 0.16;
      ctx.strokeText(word.text, word.x, word.y);
    } else if (L.style === "plain") {
      // A soft halo in the box colour (blurred stroke + shadow) under the glyphs, so the
      // text keeps its contrast on any video; lighter than an outline, never a hard edge.
      ctx.save();
      ctx.shadowColor = colors.box;
      ctx.shadowBlur = L.px * 0.35;
      ctx.strokeStyle = colors.box;
      ctx.lineWidth = L.px * 0.14;
      // Shadow only (no ctx.filter: a canvas blur filter per word is ~10x slower).
      ctx.strokeText(word.text, word.x, word.y);
      ctx.strokeText(word.text, word.x, word.y);
      ctx.restore();
      ctx.shadowColor = colors.box;
      ctx.shadowBlur = L.px * 0.2;
    }
    ctx.fillStyle = s.highlight ? colors.highlight : colors.text;
    ctx.fillText(word.text, word.x, word.y);
    if (s.underline > 0) {
      ctx.shadowBlur = 0;
      ctx.fillStyle = colors.highlight;
      const th = Math.max(2, L.px * 0.08);
      ctx.fillRect(word.x, word.y + L.px * 0.5, word.width * Math.min(1, s.underline), th);
    }
    ctx.restore();
  });
  ctx.restore();
  return [drawn[0] - L.grow, drawn[1] - L.grow, drawn[2] + L.grow, drawn[3] + L.grow];
}

module.exports = { layoutBlock, drawBlock, reflow, SCALE_GROWTH };
