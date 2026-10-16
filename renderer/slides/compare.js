// Comparison: two columns (content.left / content.right: {title, items 1-4}), each a title
// over an accent rule and its items with a short dash. Side by side where the area is wide
// enough, else stacked. Two build steps: the left column, then the right one.

const S = require("../lib/slide");

const name = "compare";
const steps = () => 2;

function column(ctx, env, col, z, w) {
  const indent = Math.round(z.body * 0.85);
  const title = S.run(ctx, env, col.title, { px: z.heading, role: "heading", maxWidth: w, maxLines: 2 });
  const items = col.items.map((s) => S.run(ctx, env, s, { px: z.body, role: "body", maxWidth: w - indent, maxLines: 3 }));
  const rule = { h: Math.max(3, Math.round(z.heading * 0.07)), gap: Math.round(z.heading * 0.35) };
  const gap = Math.round(z.body * 0.5);
  const h = title.height + 2 * rule.gap + rule.h + items.reduce((a, r) => a + r.height, 0) + gap * (items.length - 1);
  return { title, items, rule, gap, indent, w, h, over: title.over || items.some((r) => r.over) };
}

function layout(ctx, slide, env) {
  const c = slide.content;
  const L = S.fit(slide.area, (k, aw, ah) => {
    const z = S.sizes(env, k);
    const gutter = Math.round(env.unit * 0.07 * k);
    const blockW = Math.min(aw, env.unit * 1.7 * k);
    const sideW = Math.floor((blockW - gutter) / 2);
    const side = [column(ctx, env, c.left, z, sideW), column(ctx, env, c.right, z, sideW)];
    const sideL = { z, cols: side, stacked: false, gutter, w: blockW, h: Math.max(side[0].h, side[1].h), over: side.some((s) => s.over) };
    if (!sideL.over && sideL.h <= ah) return sideL;
    const st = [column(ctx, env, c.left, z, blockW), column(ctx, env, c.right, z, blockW)];
    const stL = { z, cols: st, stacked: true, gutter, w: blockW, h: st[0].h + gutter + st[1].h, over: st.some((s) => s.over) };
    return !stL.over && stL.h <= ah ? stL : sideL;
  });
  L.origins = L.stacked
    ? [[L.x, L.y], [L.x, L.y + L.cols[0].h + L.gutter]]
    : [[L.x, L.y], [L.x + L.cols[0].w + L.gutter, L.y]];
  L.texts = [];
  L.places = L.cols.map((col, i) => {
    const [x, y0] = L.origins[i];
    const ruleY = y0 + col.title.height + col.rule.gap;
    let y = ruleY + col.rule.h + col.rule.gap;
    const rows = col.items.map((r) => {
      const at = y;
      y += r.height + col.gap;
      return at;
    });
    L.texts.push(S.textEntry(col.title, S.runBox(col.title, x, y0)));
    col.items.forEach((r, j) => L.texts.push(S.textEntry(r, S.runBox(r, x + col.indent, rows[j]))));
    return { x, y: y0, ruleY, rows };
  });
  return L;
}

function draw(ctx, t, slide, env, L) {
  const pal = S.palette(env.tokens);
  L.cols.forEach((col, i) => {
    const pl = L.places[i];
    const p = S.enterP(env.tokens, t - env.times[i]);
    S.enter(ctx, env, p, L.z.heading, [pl.x, pl.y, pl.x + col.w, pl.y + col.h], (c) => {
      S.drawRun(c, env, col.title, pl.x, pl.y, "left", pal.ink);
      S.roundRect(c, [pl.x, pl.ruleY, pl.x + col.w * Math.min(1, p), pl.ruleY + col.rule.h], col.rule.h / 2, i === 0 ? pal.muted : pal.accent);
      col.items.forEach((r, j) => {
        const y = pl.rows[j] + r.lineHeight / 2;
        S.line(c, pl.x, y, pl.x + L.z.body * 0.45, y, Math.max(2, L.z.body * 0.08), pal.muted);
        S.drawRun(c, env, r, pl.x + col.indent, pl.rows[j], "left", pal.ink);
      });
    });
  });
}

module.exports = { name, steps, layout, draw };
