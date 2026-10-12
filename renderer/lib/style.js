// How scenes read brand tokens. Scenes never name a colour or a font themselves; they
// ask here, and everything comes from env.tokens (or the renderer's font setup).
// Token values may be free text from a brand guide, so modes are matched by keyword.

const { step, brandSpring } = require("./spring");

// Caption colours that already pass WCAG AA (resolved by the brand loader); the raw
// brand colours are only a stand-in for tokens that were not resolved.
function captionColors(tokens) {
  const c = tokens.caption_colors || {};
  const raw = tokens.colors || {};
  return {
    text: c.text || raw.text,
    box: c.box || raw.surface || raw.bg,
    highlight: c.highlight || raw.accent || raw.text,
  };
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

// Letter spacing in px for a role ("display", "heading", "body"), from tokens in em.
function tracking(tokens, role, px) {
  const t = tokens.fonts && tokens.fonts.tracking;
  const em = t && Number.isFinite(t[role]) ? t[role] : 0;
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
  return Number.isFinite(tokens.radius_px) ? tokens.radius_px : Math.round(px * 0.28);
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
  captionColors,
  weights,
  strongest,
  font,
  tracking,
  captionSettings,
  setCase,
  transition,
  radius,
  appear,
  enter,
  bleed,
};
