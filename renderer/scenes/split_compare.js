// Split comparison (myth vs reality, before/after): two cards side by side,
// props.left and props.right, each {title, text}. The right card follows the left
// one on the brand spring; its title takes the accent, the left one stays muted.
// Colours, fonts and motion come only from env.tokens.

const style = require("../lib/style");
const card = require("../lib/card");
const material = require("../lib/material");
const { union } = require("../lib/boxes");

const name = "split_compare";

const side = (item, k) => {
  const s = (item.props && item.props[k]) || {};
  return { title: String(s.title || "").trim(), text: String(s.text || "").trim() };
};

function layout(ctx, item, env) {
  const l = side(item, "left");
  const r = side(item, "right");
  if (!(l.title || l.text || r.title || r.text)) return null;
  return env.once(item, () => {
    const { tokens } = env;
    const f = style.frame(env);
    // Landscape: the pair sits at the foot of the frame (above the caption strip), wide.
    return card.layoutCard(
      env,
      item,
      (k) => {
      const px = f.unit * 0.05 * k;
      const pad = Math.round(px * 0.6);
      const gap = Math.round(px * 0.4);
      const colW = (card.maxCardWidth(env, px, 0, true) - gap) / 2 - 2 * pad;
      const col = (s) => {
        const title = s.title ? card.textRun(ctx, env, style.setCase(tokens, s.title), { px: px * 1.15, role: "heading", maxWidth: colW, maxLines: 2 }) : null;
        const text = s.text ? card.textRun(ctx, env, s.text, { px, role: "body", maxWidth: colW, maxLines: 6 }) : null;
        const between = title && text ? Math.round(px * 0.4) : 0;
        return { title, text, between, h: (title ? title.height : 0) + between + (text ? text.height : 0) };
      };
      const left = col(side(item, "left"));
      const right = col(side(item, "right"));
      const h = Math.max(left.h, right.h) + 2 * pad;
      return { px, pad, gap, colW, left, right, w: 2 * (colW + 2 * pad) + gap, h };
      },
      { landscape: { position: "lower", anchor: "center" } },
    );
  });
}

function draw(ctx, t, item, env) {
  const L = layout(ctx, item, env);
  if (!L) return null;
  const { tokens } = env;
  const pal = card.palette(tokens);
  const dur = item.t1 - item.t0;
  const lag = card.reachTime(tokens) * 0.5;
  const cw = L.colW + 2 * L.pad;
  const r = style.radius(tokens, L.px);
  const column = (c, x, y, col, k, titleColor) => {
    const a = style.appear(tokens, t - k, dur - k);
    if (a.p <= 0) return null;
    c.save();
    const drawn = style.enter(c, tokens, a, L.px, [x, y, x + cw, L.box[3]]);
    const h = L.box[3] - L.box[1];
    material.plate(c, env, (k) => {
      k.beginPath();
      k.roundRect(x, y, cw, h, r);
    }, { box: [x, y, x + cw, y + h], px: L.px, color: pal.fill });
    material.vibrancy(c, env, L.px);
    let ty = y + L.pad;
    if (col.title) {
      card.drawRun(c, env, col.title, x + L.pad, ty, "left", titleColor);
      ty += col.title.height + col.between;
    }
    if (col.text) card.drawRun(c, env, col.text, x + L.pad, ty, "left", pal.text);
    c.restore();
    return drawn;
  };
  const [x0, y0] = L.box;
  const left = column(ctx, x0, y0, L.left, 0, pal.muted);
  const right = column(ctx, x0 + cw + L.gap, y0, L.right, lag, pal.accent);
  return union(left, right);
}

module.exports = { name, layout, draw };
