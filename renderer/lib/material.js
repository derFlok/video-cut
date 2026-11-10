// The material every card plate of the canvas scenes is drawn in: the brand's
// motion.depth (flat | soft | glow | glass; default glow on a dark plate, soft on a light
// one, like the motion runtime). Colours come only from the brand tokens (via
// card.palette); shadows and highlights are those colours at an alpha.
//
// glass: the plate is the caption box colour at a tint alpha the vibrancy guard chose
// (lib/glass.js), with a soft shadow OUTSIDE the shape only, a top sheen, grain and a 1 px
// specular edge lit from the frame's top-left (so the highlight moves when the plate
// moves). The same shape is filled into env.mask (the glass mask canvas, when the renderer
// keeps one) with the context's transform, alpha and filter, so the frost FFmpeg puts under
// the plate follows its entrance and exit exactly.

const G = require("./glass");

const DEPTHS = ["flat", "soft", "glow", "glass"];
const cache = new WeakMap();

function pick(v, allowed) {
  const hit = String(v || "").toLowerCase().match(/[a-z]+/g) || [];
  return hit.find((w) => allowed.includes(w)) || null;
}

// Resolved material of a token set: {depth, dark, fill, text, accent, muted, shade, white,
// tint: {alpha, densified, contrast} (glass only)}. Memoised per tokens object.
function resolve(tokens) {
  if (cache.has(tokens)) return cache.get(tokens);
  const raw = tokens.colors || {};
  const cap = tokens.caption_colors || {};
  const fill = cap.box || raw.surface || raw.bg;
  const text = cap.text || raw.text;
  const accent = cap.highlight || raw.accent || raw.text;
  // (on glass `accent` becomes the lifted accent text, see below)
  const dark = G.rgb(fill) ? G.lum(G.rgb(fill)) < 0.18 : false;
  const depth = pick(tokens.motion && tokens.motion.depth, DEPTHS) || (dark ? "glow" : "soft");
  // The brand's darkest base colour carries the shadows.
  const cands = [raw.text, raw.bg, raw.surface, text, fill].filter((c) => G.rgb(c));
  const shade = cands.reduce((a, b) => (G.lum(G.rgb(b)) < G.lum(G.rgb(a)) ? b : a), cands[0]);
  const m = { depth, dark, fill, text, accent, muted: raw.muted || text, shade, white: G.hex([255, 255, 255]) };
  if (depth === "glass") {
    const base = G.TINT[dark ? "dark" : "light"];
    const range = G.span(tokens.glass_backdrop);
    // text needs 4.5:1 on the glass; the accent (the highlight word, big figures: large
    // text) 3:1
    // text needs 4.5:1 on the glass (a denser tint when the material's own misses); the
    // accent (the highlight word, figures: large text) is lifted to 3:1 instead
    m.tint = G.tint(fill, text, base, G.MIN_CONTRAST, range);
    m.tint.base = base;
    m.tint.range = range;
    m.accent = G.accentOn(fill, accent, m.tint.alpha, range);
    m.tint.accent = m.accent;
  }
  cache.set(tokens, m);
  return m;
}

function grainPattern(ctx, env) {
  if (!env.canvas) return null;
  if (!env.__grain) {
    const g = G.grain();
    const cv = env.canvas(g.width, g.height);
    const c = cv.getContext("2d");
    const img = c.createImageData(g.width, g.height);
    img.data.set(g.data);
    c.putImageData(img, 0, 0);
    env.__grain = cv;
  }
  return ctx.createPattern(env.__grain, "repeat");
}

// Box [x0, y0, x1, y1] of the current path's shape, for gradients (the caller passes it).
// Draw a plate: `shape(c)` builds the path on context c (beginPath + roundRect etc.).
// opt: {box (for gradients), px (type size: scales shadows), color (fill instead of the
// material's), solid (true: no glass for this plate, e.g. an accent bubble)}.
function plate(ctx, env, shape, opt = {}) {
  const m = resolve(env.tokens);
  const color = opt.color || m.fill;
  const px = opt.px || 40;
  const box = opt.box;
  const kind = opt.solid ? (m.depth === "glass" ? "soft" : m.depth) : m.depth;
  if (kind === "flat") {
    shape(ctx);
    ctx.fillStyle = color;
    ctx.fill();
    return;
  }
  if (kind === "soft" || kind === "glow") {
    ctx.save();
    shape(ctx);
    ctx.fillStyle = color;
    ctx.shadowColor = kind === "glow" ? G.withAlpha(m.accent, 0.38) : G.withAlpha(m.shade, m.dark ? 0.5 : 0.2);
    ctx.shadowBlur = px * (kind === "glow" ? 0.9 : 0.8);
    ctx.shadowOffsetY = kind === "glow" ? 0 : px * 0.16;
    ctx.fill();
    // a tighter contact shadow under the soft one
    ctx.shadowColor = G.withAlpha(m.shade, m.dark ? 0.35 : 0.1);
    ctx.shadowBlur = px * 0.12;
    ctx.shadowOffsetY = px * 0.03;
    ctx.fill();
    ctx.restore();
    if (kind === "glow" || m.dark) edge(ctx, shape, kind === "glow" ? G.withAlpha(m.accent, 0.42) : G.withAlpha(m.white, 0.08), px);
    return;
  }
  // glass
  const a = m.tint.alpha;
  ctx.save();
  // shadow outside the shape only (the frost shows through the tint, a shadow under it
  // would muddy it): clip to everything but the shape, fill with a shadow
  ctx.beginPath();
  const big = 1e5;
  ctx.rect(-big, -big, 2 * big, 2 * big);
  shapeInto(ctx, shape);
  ctx.clip("evenodd");
  shape(ctx);
  ctx.fillStyle = color;
  ctx.shadowColor = G.withAlpha(m.shade, m.dark ? 0.42 : 0.2);
  ctx.shadowBlur = px * 1.1;
  ctx.shadowOffsetY = px * 0.22;
  ctx.fill();
  ctx.restore();
  // tint
  ctx.save();
  shape(ctx);
  ctx.globalAlpha *= a;
  ctx.fillStyle = color;
  ctx.fill();
  ctx.restore();
  // sheen (lighter at the top) and grain
  ctx.save();
  shape(ctx);
  ctx.clip();
  const g = box ? ctx.createLinearGradient(0, box[1], 0, box[3]) : null;
  if (g && g.addColorStop) {
    g.addColorStop(0, G.withAlpha(m.white, m.dark ? 0.1 : 0.22));
    g.addColorStop(0.5, G.withAlpha(m.white, 0));
    ctx.fillStyle = g;
    ctx.fillRect(box[0], box[1], box[2] - box[0], box[3] - box[1]);
  }
  const pat = grainPattern(ctx, env);
  if (pat && box) {
    ctx.fillStyle = pat;
    ctx.fillRect(box[0], box[1], box[2] - box[0], box[3] - box[1]);
  }
  ctx.restore();
  // specular edge: light from the frame's top-left, darker towards the bottom-right; the
  // gradient is anchored to the frame, so the highlight slides along the edge as the
  // plate moves
  const [W, H] = env.size || [1080, 1920];
  const T = ctx.getTransform();
  if (T && T.inverse && typeof DOMPoint === "function") {
    const inv = T.inverse();
    const p0 = inv.transformPoint(new DOMPoint(0, 0));
    const p1 = inv.transformPoint(new DOMPoint(W, H));
    const s = ctx.createLinearGradient(p0.x, p0.y, p1.x, p1.y);
    s.addColorStop(0, G.withAlpha(m.white, m.dark ? 0.5 : 0.85));
    s.addColorStop(0.55, G.withAlpha(m.white, m.dark ? 0.14 : 0.35));
    s.addColorStop(1, G.withAlpha(m.shade, m.dark ? 0.35 : 0.16));
    edge(ctx, shape, s, px);
  }
  // mask
  if (env.mask) {
    const k = env.mask;
    k.save();
    k.setTransform(T);
    k.globalAlpha = ctx.globalAlpha;
    k.filter = ctx.filter;
    shape(k);
    k.fillStyle = m.white;
    k.fill();
    k.restore();
    env.maskUsed = true;
  }
}

// Append the shape's path to the current path (shape() starts with beginPath, so replay
// it on a Path2D-like recorder): we call shape on a proxy that ignores beginPath.
function shapeInto(ctx, shape) {
  const proxy = new Proxy(ctx, {
    get(target, prop) {
      if (prop === "beginPath") return () => {};
      const v = target[prop];
      return typeof v === "function" ? v.bind(target) : v;
    },
  });
  shape(proxy);
}

// A 1 px (at least) inner edge in `stroke` (a colour or gradient), clipped to the shape.
function edge(ctx, shape, stroke, px) {
  ctx.save();
  shape(ctx);
  ctx.clip();
  shape(ctx);
  ctx.lineWidth = Math.max(2, px * 0.05);
  ctx.strokeStyle = stroke;
  ctx.stroke();
  ctx.restore();
}

// Ink colours on the material: on glass the text keeps a faint halo in the tint colour
// (vibrancy: text stays crisp over any frost).
function vibrancy(ctx, env, px) {
  const m = resolve(env.tokens);
  if (m.depth !== "glass") return;
  ctx.shadowColor = G.withAlpha(m.fill, 0.55);
  ctx.shadowBlur = px * 0.18;
}

// Report of the material for the renderer summary.
function report(tokens) {
  const m = resolve(tokens);
  return m.depth === "glass"
    ? { depth: m.depth, tint_alpha: m.tint.alpha, tint_base: m.tint.base, densified: m.tint.densified, contrast_worst: m.tint.contrast, backdrop: m.tint.range, accent_text: m.accent }
    : { depth: m.depth };
}

module.exports = { DEPTHS, resolve, plate, edge, vibrancy, report };
