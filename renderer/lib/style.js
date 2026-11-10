// How scenes read brand tokens. Scenes never name a colour or a font themselves; they
// ask here, and everything comes from env.tokens (or the renderer's font setup).
// Token values may be free text from a brand guide, so modes are matched by keyword.

const { step, brandSpring } = require("./spring");

// Caption colours that already pass WCAG AA (resolved by the brand loader); the raw
// brand colours are only a stand-in for tokens that were not resolved.
// On glass the highlight is the accent lifted to keep its contrast over the frost
// (lib/material.js).
function captionColors(tokens) {
  const c = tokens.caption_colors || {};
  const raw = tokens.colors || {};
  const m = require("./material").resolve(tokens);
  // on glass the material decides the pair (light glass takes the brand's dark ink)
  if (m.depth === "glass") return { text: m.text, box: m.fill, highlight: m.accent };
  return {
    text: c.text || raw.text,
    box: c.box || raw.surface || raw.bg,
    highlight: c.highlight || raw.accent || raw.text,
  };
}

// The frame a scene lays out in: width/height from the renderer (env.width/env.height,
// else env.size), 1080x1920 when none is given. `unit` is the short side: the type scale
// follows it, so text is as large on a 1920x1080 frame as on a 1080x1920 one. `aspect`
// is "portrait" (h/w > 1.2), "landscape" (w/h > 1.2) or "square".
function frame(env) {
  const size = env.size || [];
  const w = Number(env.width) > 0 ? Number(env.width) : Number(size[0]) > 0 ? Number(size[0]) : 1080;
  const h = Number(env.height) > 0 ? Number(env.height) : Number(size[1]) > 0 ? Number(size[1]) : 1920;
  const aspect = h / w > 1.2 ? "portrait" : w / h > 1.2 ? "landscape" : "square";
  // `tall`: 9:16-like frames, where the bands of the vertical stack have room; the
  // other frames (landscape, square, 4:5) keep a strip at the foot for the caption.
  return { w, h, unit: Math.min(w, h), aspect, landscape: aspect === "landscape", tall: h / w >= 1.6 };
}

// Font size of a caption: a share of the short side; a bit smaller in a frame that is not
// tall (landscape, square, 4:5), where the block sits in front of the picture and the
// vertical room is short.
function captionPx(env) {
  const f = frame(env);
  return f.unit * (f.tall ? 0.07 : 0.055);
}

// Room a bottom-centre caption block of a landscape frame takes at the foot of the
// allowed area: two lines with padding, plus the transition bleed above and below (the
// blocks beside it keep that far from it, whichever is laid out first).
function captionReserve(env) {
  const px = captionPx(env);
  return Math.ceil(2 * Math.round(px * 1.18) + 2 * Math.round(px * 0.25) + 2 * bleed(px)) + 2;
}

function weights(tokens) {
  const ws = ((tokens.fonts && tokens.fonts.weights) || []).filter(Number.isFinite);
  return ws.length ? ws : [400, 700];
}

const strongest = (tokens) => Math.max(...weights(tokens));

// CSS font for the canvas. env.fonts.family is the family list the renderer set up
// (the brand's own files when it has some, the system UI font otherwise).
function font(env, px, weight = strongest(env.tokens)) {
  return `${weight} ${Math.round(px)}px ${env.fonts.family}`;
}

// Letter spacing in px for a role ("display", "heading", "body", "label"), from tokens in
// em. Without a brand value the tracking follows the size, as optical sizing does: large
// display type tightens, small labels open up a little, body text stays at 0.
const TRACK = { display: -0.025, heading: -0.012, body: 0, label: 0.012 };
function tracking(tokens, role, px) {
  const t = tokens.fonts && tokens.fonts.tracking;
  const em = t && Number.isFinite(t[role]) ? t[role] : TRACK[role] || 0;
  return `${(em * px).toFixed(2)}px`;
}

function captionSettings(tokens) {
  const c = tokens.captions || {};
  const has = (v, word) => String(v || "").toLowerCase().includes(word);
  const hl = c.highlight || "accent";
  return {
    style: has(c.style, "outline") ? "outline" : has(c.style, "plain") ? "plain" : "box",
    upper: has(c.case, "upper"),
    position: has(c.position, "lower") ? "lower" : "center",
    highlight: {
      accent: has(hl, "accent"),
      underline: has(hl, "underline"),
      scale: has(hl, "scale"),
    },
  };
}

function setCase(tokens, text) {
  return captionSettings(tokens).upper ? text.toLocaleUpperCase() : text;
}

function transition(tokens) {
  const t = String((tokens.motion && tokens.motion.transitions) || "").toLowerCase();
  if (t.includes("slide")) return "slide";
  if (t.includes("cut")) return "cut";
  return "blur";
}

function radius(tokens, px) {
  return Number.isFinite(tokens.radius_px) ? tokens.radius_px : Math.round(px * 0.4);
}

// Visibility of an item `t` seconds into its life of `dur` seconds: the brand spring
// in, the same spring mirrored (twice as fast) out. `p` is clamped to 0..1 for
// opacity, `move` keeps the overshoot for position and scale.
function appear(tokens, t, dur) {
  if (t < 0 || t > dur) return { p: 0, move: 0 };
  if (transition(tokens) === "cut") return { p: 1, move: 1 };
  const { w, z } = brandSpring(tokens);
  const move = Math.min(step(t, w, z), step((dur - t) * 2, w, z));
  return { p: Math.max(0, Math.min(1, move)), move };
}

// Apply the brand transition for visibility `a` (from appear) to the context, around
// the centre of `box`; the caller wraps it in save()/restore(). `px` scales the slide
// distance and blur radius. Returns the box as drawn (moved, scaled, blur-grown).
function enter(ctx, tokens, a, px, box) {
  const kind = transition(tokens);
  ctx.globalAlpha *= a.p;
  if (kind === "blur") {
    const r = (1 - a.p) * px * 0.25;
    if (r > 0.05) ctx.filter = `blur(${r.toFixed(2)}px)`;
    const s = 0.94 + 0.06 * a.move;
    const cx = (box[0] + box[2]) / 2;
    const cy = (box[1] + box[3]) / 2;
    ctx.translate(cx, cy);
    ctx.scale(s, s);
    ctx.translate(-cx, -cy);
    const grow = 3 * r; // a CSS blur(r) is a Gaussian with sigma r
    return [
      cx + (box[0] - cx) * s - grow,
      cy + (box[1] - cy) * s - grow,
      cx + (box[2] - cx) * s + grow,
      cy + (box[3] - cy) * s + grow,
    ];
  }
  if (kind === "slide") {
    const dy = (1 - a.move) * px * 0.6;
    ctx.translate(0, dy);
    return [box[0], box[1] + dy, box[2], box[3] + dy];
  }
  return box.slice();
}

// How far a transition can draw outside a block's layout box; placement keeps this
// much distance from the safe zones so the drawn pixels stay out of them too.
function bleed(px) {
  return Math.ceil(px * 0.8);
}

module.exports = {
  frame,
  captionPx,
  captionReserve,
  captionColors,
  weights,
  strongest,
  font,
  tracking,
  TRACK,
  captionSettings,
  setCase,
  transition,
  radius,
  appear,
  enter,
  bleed,
};
