// End card: a full-frame plate (colors.bg, or the darker of bg/text with
// props.plate "dark") with the call to action (props.text) and, with props.logo,
// the brand logo. The logo variant is chosen once by the plate's luminance (the
// "dark" variant on a dark plate), only one variant is ever drawn, and only while
// the plate is fully opaque, so a logo never sits directly over video. Logo rules
// that are numbers (logo.min_width_px, logo.clear_space as a share of the logo
// height) are respected; logo.placement "none" means no logo.
// Colours, fonts and motion come only from env.tokens.

const style = require("../lib/style");
const card = require("../lib/card");

const name = "cta_end";

const textOf = (item) => String((item.props && item.props.text) || "").trim();
const isDark = (item) => String((item.props && item.props.plate) || "").toLowerCase() === "dark";

// The one logo file to use on this plate, or null.
function logoRef(item, tokens) {
  const logo = tokens.logo || {};
  if (!(item.props && item.props.logo) || String(logo.placement || "").toLowerCase() === "none") return null;
  const lum = card.luminance(card.plateColors(tokens, isDark(item)).plate);
  const variant = lum != null && lum < 0.18 ? "dark" : "light";
  const ref = logo[variant] || logo[`icon_${variant}`];
  return typeof ref === "string" && ref ? ref : null;
}

// Files the renderer has to load for this item ("brand": the brand's own assets).
function assets(item, tokens) {
  const ref = logoRef(item, tokens || {});
  return ref ? [{ ref, root: "brand" }] : [];
}

const num = (v, lo, hi) => (Number.isFinite(v) && v >= lo && v <= hi ? v : null);

function layout(ctx, item, env) {
  return env.once(item, () => {
    const { tokens, size, safe } = env;
    const s = safe || {};
    const area = [s.side || 0, s.top || 0, size[0] - (s.side || 0), size[1] - (s.bottom || 0)];
    const aw = area[2] - area[0];
    const ah = area[3] - area[1];
    const px = size[0] * 0.09;
    const ref = logoRef(item, tokens);
    const img = ref ? env.image(ref) : null;
    const rules = { ...(tokens.logo || {}), ...((tokens.logo && typeof tokens.logo.rules === "object" && tokens.logo.rules) || {}) };
    let logo = null;
    if (img && img.width > 0 && img.height > 0) {
      const ar = img.width / img.height;
      let lh = Math.min(ah * 0.16, (aw * 0.6) / ar);
      const minW = num(rules.min_width_px, 1, size[0]);
      if (minW && lh * ar < minW) lh = Math.min(minW / ar, ah * 0.4, aw / ar);
      const clear = num(rules.clear_space, 0, 4) ?? 0.5;
      logo = { ref, w: lh * ar, h: lh, gap: Math.round(lh * clear) };
    }
    const text = textOf(item)
      ? card.textRun(ctx, env, style.setCase(tokens, textOf(item)), { px, role: "display", maxWidth: aw - 2 * style.bleed(px), maxLines: 4, minPx: px * 0.5 })
      : null;
    const h = (logo ? logo.h + (text ? logo.gap : 0) : 0) + (text ? text.height : 0);
    const top = Math.round(area[1] + (ah - h) / 2);
    return {
      px,
      area,
      logo,
      text,
      top,
      fits: h <= ah,
      overlapsFace: false,
      overlapsBlock: false,
    };
  });
}

function draw(ctx, t, item, env) {
  const L = layout(ctx, item, env);
  const { tokens, size } = env;
  const dur = item.t1 - item.t0;
  // Plate: monotone in on the brand spring, out twice as fast; content lives inside
  // the time the plate is fully opaque.
  const tr = style.transition(tokens) === "cut" ? 0 : card.reachTime(tokens);
  const plateA = Math.min(card.settle(tokens, t), card.settle(tokens, (dur - t) * 2));
  if (!(plateA > 0)) return null;
  const { plate, ink } = card.plateColors(tokens, isDark(item));
  ctx.save();
  ctx.globalAlpha = plateA;
  ctx.fillStyle = plate;
  ctx.fillRect(0, 0, size[0], size[1]);
  ctx.restore();
  const ct = t - tr;
  const cdur = dur - 1.5 * tr;
  const solid = plateA >= 1;
  if (!solid || !(cdur > 0) || !(L.logo || L.text)) return [0, 0, size[0], size[1]];
  const a = style.appear(tokens, ct, cdur);
  if (a.p > 0) {
    const cx = size[0] / 2;
    const h = (L.logo ? L.logo.h + (L.text ? L.logo.gap : 0) : 0) + (L.text ? L.text.height : 0);
    ctx.save();
    style.enter(ctx, tokens, a, L.px, [cx - 1, L.top, cx + 1, L.top + h]);
    let y = L.top;
    if (L.logo) {
      ctx.drawImage(env.image(L.logo.ref), cx - L.logo.w / 2, y, L.logo.w, L.logo.h);
      y += L.logo.h + L.logo.gap;
    }
    if (L.text) card.drawRun(ctx, env, L.text, cx, y, "center", ink);
    ctx.restore();
  }
  return [0, 0, size[0], size[1]];
}

module.exports = { name, assets, layout, draw, logoRef };
