// Bar chart: an optional title, then 2-6 horizontal bars ({label, value}) with the value
// (and content.unit) at each bar's end. A bar grows when its build step comes. With a
// sparing accent only the largest bar takes it; the others are in the muted ink.

const S = require("../lib/slide");

const name = "bar_chart";
const steps = (c) => c.bars.length;

function valueText(v, unit, lang) {
  const s = S.formatValue(v, lang);
  if (!unit) return s;
  return /^[%‰°]/.test(unit) ? `${s}${lang === "en" ? "" : " "}${unit}` : `${s} ${unit}`;
}

function layout(ctx, slide, env) {
  const c = slide.content;
  const unit = String(c.unit || "").trim();
  const L = S.fit(slide.area, (k, aw) => {
    const z = S.sizes(env, k);
    const blockW = Math.min(aw, env.unit * 1.6 * k);
    const title = c.title ? S.run(ctx, env, c.title, { px: z.heading, role: "heading", maxWidth: blockW, maxLines: 2 }) : null;
    const head = title ? title.height + Math.round(z.heading * 0.7) : 0;
    const labels = c.bars.map((b) => S.run(ctx, env, b.label, { px: z.label, role: "body", maxWidth: blockW, maxLines: 1 }));
    const values = c.bars.map((b) => S.run(ctx, env, valueText(Number(b.value), unit, env.lang), { px: z.body, role: "heading", maxWidth: blockW * 0.35, maxLines: 1 }));
    const barH = Math.round(z.body * 0.8);
    // The bar row is as tall as the bar or its value text, whichever is taller.
    const barRow = Math.max(barH, ...values.map((v) => v.height));
    const labelGap = Math.round(z.label * 0.25);
    const rowGap = Math.round(z.body * 0.6);
    const rowH = Math.max(...labels.map((l) => l.height)) + labelGap + barRow;
    const n = c.bars.length;
    return {
      z,
      title,
      head,
      labels,
      values,
      barH,
      barRow,
      labelGap,
      rowGap,
      rowH,
      valueW: Math.max(...values.map((v) => v.width)),
      w: blockW,
      h: head + n * rowH + (n - 1) * rowGap,
      over: Boolean(title && title.over) || [...labels, ...values].some((r) => r.over),
    };
  });
  const vals = c.bars.map((b) => Math.max(0, Number(b.value)));
  const max = Math.max(...vals) || 1;
  L.track = L.w - L.valueW - L.z.body * 0.4;
  L.bars = vals.map((v, i) => {
    const y = L.y + L.head + i * (L.rowH + L.rowGap);
    const row = y + L.labels[i].height + L.labelGap; // top of the bar row
    return { y, by: row + (L.barRow - L.barH) / 2, vy: row + (L.barRow - L.values[i].height) / 2, len: (L.track * v) / max, top: v === max };
  });
  L.texts = [];
  if (L.title) L.texts.push(S.textEntry(L.title, S.runBox(L.title, L.x, L.y)));
  L.bars.forEach((b, i) => {
    L.texts.push(S.textEntry(L.labels[i], S.runBox(L.labels[i], L.x, b.y)));
    const vx = L.x + b.len + L.z.body * 0.3;
    L.texts.push(S.textEntry(L.values[i], S.runBox(L.values[i], vx, b.vy)));
  });
  return L;
}

function draw(ctx, t, slide, env, L) {
  const pal = S.palette(env.tokens);
  const { tokens } = env;
  if (L.title)
    S.enter(ctx, env, S.enterP(tokens, t - 0.1), L.z.heading, S.runBox(L.title, L.x, L.y), (c) =>
      S.drawRun(c, env, L.title, L.x, L.y, "left", pal.ink),
    );
  L.bars.forEach((b, i) => {
    const start = env.times[i];
    const p = S.enterP(tokens, t - start);
    if (!(p > 0)) return;
    const base = ctx.globalAlpha;
    ctx.globalAlpha = base * p;
    S.drawRun(ctx, env, L.labels[i], L.x, b.y, "left", pal.ink);
    ctx.globalAlpha = base;
    // The bar grows a little behind its label, calmly (slower than an entrance).
    const g = S.enterP(tokens, (t - start - 0.1) * 0.6);
    const w = b.len * g;
    if (w > 0) S.roundRect(ctx, [L.x, b.by, L.x + Math.max(w, L.barH * 0.2), b.by + L.barH], Math.min(L.barH / 2, L.barH * 0.3), pal.sparing && !b.top ? pal.muted : pal.accent);
    ctx.globalAlpha = base * g;
    const v = L.values[i];
    S.drawRun(ctx, env, v, L.x + w + L.z.body * 0.3, b.vy, "left", pal.ink);
    ctx.globalAlpha = base;
  });
}

module.exports = { name, steps, layout, draw, valueText };
