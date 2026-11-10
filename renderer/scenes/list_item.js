// List item: an animated number badge (props.index) and the point's text
// (props.text); with props.total, a row of progress pips under the text.
// Colours, fonts and motion come only from env.tokens.

const style = require("../lib/style");
const card = require("../lib/card");

const name = "list_item";

const textOf = (item) => String((item.props && item.props.text) || "").trim();
const indexOf = (item) => String((item.props && item.props.index) ?? "").trim();
const totalOf = (item) => {
  const n = Number(item.props && item.props.total);
  return Number.isInteger(n) && n > 0 && n <= 12 ? n : 0;
};

function layout(ctx, item, env) {
  if (!textOf(item)) return null;
  return env.once(item, () => {
    const { tokens } = env;
    const unit = style.frame(env).unit;
    return card.layoutCard(env, item, (k) => {
      const px = unit * 0.062 * k;
      const pad = Math.round(px * 0.5);
      const badge = Math.round(px * 1.9);
      const gap = Math.round(px * 0.5);
      const num = card.textRun(ctx, env, style.setCase(tokens, indexOf(item) || "•"), {
        px: px * 1.1,
        role: "display",
        maxWidth: badge * 0.8,
        maxLines: 1,
        minPx: px * 0.5,
      });
      const maxW = card.maxCardWidth(env, px, pad) - badge - gap;
      const text = card.textRun(ctx, env, textOf(item), { px, role: "heading", maxWidth: maxW, maxLines: 3 });
      const total = totalOf(item);
      const pip = total ? Math.max(4, Math.round(px * 0.18)) : 0;
      const pips = total ? { r: pip, gap: pip * 2.2, top: Math.round(px * 0.35) } : null;
      const pipsW = total ? total * pip * 2 + (total - 1) * (pips.gap - pip * 2) : 0;
      const bodyH = text.height + (pips ? pips.top + 2 * pip : 0);
      return {
        px,
        pad,
        badge,
        gap,
        num,
        text,
        pips,
        total,
        w: badge + gap + Math.max(text.width, pipsW) + 2 * pad,
        h: Math.max(badge, bodyH) + 2 * pad,
      };
    });
  });
}

function draw(ctx, t, item, env) {
  const L = layout(ctx, item, env);
  if (!L) return null;
  const { tokens } = env;
  const pal = card.palette(tokens);
  const dur = item.t1 - item.t0;
  return card.drawCard(ctx, env, L, t, dur, (c, box) => {
    const x0 = box[0] + L.pad;
    const y0 = box[1] + L.pad;
    const inner = box[3] - box[1] - 2 * L.pad;
    // Badge pops in on the brand spring (overshoot kept, clamped to the badge cell).
    const pop = Math.min(1.08, Math.max(0, style.appear(tokens, t - 0.08, dur).move));
    const bx = x0 + L.badge / 2;
    const by = y0 + inner / 2;
    c.save();
    c.translate(bx, by);
    c.scale(pop, pop);
    c.translate(-bx, -by);
    c.fillStyle = pal.accent;
    c.beginPath();
    c.roundRect(x0, by - L.badge / 2, L.badge, L.badge, L.badge / 2);
    c.fill();
    card.drawTabular(c, env, L.num, L.num.rows.map((r) => r.text).join(" "), bx, by - L.num.height / 2, "center", pal.fill);
    c.restore();
    const tx = x0 + L.badge + L.gap;
    const bodyH = L.text.height + (L.pips ? L.pips.top + 2 * L.pips.r : 0);
    const ty = y0 + (inner - bodyH) / 2;
    card.drawRun(c, env, L.text, tx, ty, "left", pal.text);
    if (L.pips) {
      const idx = Number(indexOf(item));
      const py = ty + L.text.height + L.pips.top + L.pips.r;
      const base = c.globalAlpha;
      for (let i = 0; i < L.total; i++) {
        c.fillStyle = i + 1 === idx ? pal.accent : pal.muted;
        c.globalAlpha = base * (i + 1 === idx ? 1 : 0.45);
        c.beginPath();
        c.arc(tx + L.pips.r + i * L.pips.gap, py, L.pips.r, 0, Math.PI * 2);
        c.fill();
      }
    }
  });
}

module.exports = { name, layout, draw };
