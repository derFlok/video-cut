// AI label (EU AI Act Art. 50): a small pill with props.text ("KI-generiert" /
// "AI-generated") in a top corner of the safe area while AI-generated material is on
// screen. `videocut tool overlays` adds these items from work/material.json; the EDL never
// carries them. The pill sits in the corner opposite the brand logo (tokens.logo.corner
// when logo.placement is "corner"; top-left otherwise), moves to the other corner or
// beside the face when the face is there, is laid out before every other item (they keep
// clear of it), is drawn on top of everything (`top`) and also over a full-frame slide
// (`badge`). No motion: the label is fully visible for its whole span.
// Colours and font come only from env.tokens.

const style = require("../lib/style");
const { overlapArea } = require("../lib/layout");
const material = require("../lib/material");

const name = "ai_badge";
const top = true;
const badge = true;

const textOf = (item) => String((item.props && item.props.text) || "").trim();

// "left" or "right": the side the brand logo does not use.
function side(tokens) {
  const logo = (tokens && tokens.logo) || {};
  const corner = String(logo.corner || "").toLowerCase();
  return String(logo.placement || "").toLowerCase() === "corner" && corner.includes("left") ? "right" : "left";
}

function colors(tokens) {
  const c = (tokens && tokens.colors) || {};
  return { fill: c.surface || c.bg, text: c.text || c.muted, edge: c.muted || c.text };
}

function layoutFirst(item) {
  return Boolean(textOf(item));
}

function layout(ctx, item, env) {
  const text = textOf(item);
  if (!text) return null;
  return env.once(item, () => {
    const f = style.frame(env);
    const safe = env.safe || {};
    const px = Math.round(f.unit * 0.026);
    const weights = ((env.tokens.fonts && env.tokens.fonts.weights) || []).filter(Number.isFinite);
    const strong = weights.filter((w) => w >= 500);
    const weight = strong.length ? Math.min(...strong) : weights.length ? Math.max(...weights) : 600;
    ctx.font = style.font(env, px, weight);
    const padX = Math.round(px * 0.7);
    const padY = Math.round(px * 0.4);
    const w = Math.ceil(ctx.measureText(text).width) + 2 * padX;
    const h = Math.round(px * 1.2) + 2 * padY;
    const x0 = { left: safe.side || 0, right: f.w - (safe.side || 0) - w };
    const y = safe.top || 0;
    const face = env.faceDuring ? env.faceDuring(item.t0, item.t1) : null;
    const at = (s, yy) => [x0[s], yy, x0[s] + w, yy + h];
    const first = side(env.tokens);
    const other = first === "left" ? "right" : "left";
    let box = at(first, y);
    if (face && overlapArea(box, face) > 0) {
      box = at(other, y);
      // Both top corners on the face: just below it, in the preferred corner.
      if (overlapArea(box, face) > 0) box = at(first, Math.min(face[3] + px, f.h - (safe.bottom || 0) - h));
    }
    if (env.addBlock) env.addBlock(item.t0, item.t1, box);
    return {
      box,
      px,
      weight,
      padX,
      text,
      fits: true,
      overlapsFace: Boolean(face && overlapArea(box, face) > 0),
    };
  });
}

function draw(ctx, t, item, env) {
  const L = layout(ctx, item, env);
  if (!L) return null;
  const [x0, y0, x1, y1] = L.box;
  const c = colors(env.tokens);
  const r = (y1 - y0) / 2;
  const pill = (k) => {
    k.beginPath();
    k.roundRect(x0, y0, x1 - x0, y1 - y0, r);
  };
  // a solid pill in the brand's material (never glass: the legal label must read on any
  // picture), with a fine edge in the muted colour
  material.plate(ctx, env, pill, { box: L.box, px: L.px, color: c.fill, solid: true });
  material.edge(ctx, pill, c.edge, L.px * 0.9);
  ctx.font = style.font(env, L.px, L.weight);
  ctx.fillStyle = c.text;
  ctx.textBaseline = "middle";
  ctx.fillText(L.text, x0 + L.padX, (y0 + y1) / 2);
  return L.box.slice();
}

module.exports = { name, top, badge, layoutFirst, layout, draw, side };
