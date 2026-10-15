// Steps: an optional title, then 2-5 numbered steps joined by arrows, a flow from left to
// right where the area is wide, else from top to bottom. Each step (badge, text and the
// arrow leading to it) is one build step.

const S = require("../lib/slide");

const name = "steps";
const steps = (c) => c.steps.length;

function measure(ctx, env, c, k, aw, horizontal) {
  const z = S.sizes(env, k);
  const blockW = Math.min(aw, env.unit * 1.7 * k);
  const title = c.title ? S.run(ctx, env, c.title, { px: z.heading, role: "heading", maxWidth: blockW, maxLines: 2 }) : null;
  const head = title ? title.height + Math.round(z.heading * 0.7) : 0;
  const badge = Math.round(z.body * 1.5);
  const n = c.steps.length;
  if (horizontal) {
    const arrowW = Math.round(z.body * 1.3);
    const colW = Math.floor((blockW - (n - 1) * arrowW) / n);
    const runs = c.steps.map((s) => S.run(ctx, env, s, { px: z.body, role: "body", maxWidth: colW, maxLines: 4 }));
    const textH = Math.max(...runs.map((r) => r.height));
    const gap = Math.round(z.body * 0.45);
    return { horizontal, title, head, badge, runs, arrowW, colW, gap, w: blockW, h: head + badge + gap + textH, over: Boolean(title && title.over) || runs.some((r) => r.over) };
  }
  const indent = badge + Math.round(z.body * 0.6);
  const runs = c.steps.map((s) => S.run(ctx, env, s, { px: z.body, role: "body", maxWidth: blockW - indent, maxLines: 2 }));
  const arrowH = Math.round(z.body * 1.4);
  const rowH = runs.map((r) => Math.max(badge, r.height));
  return {
    horizontal,
    title,
    head,
    badge,
    runs,
    indent,
    arrowH,
    rowH,
    w: Math.max(title ? title.width : 0, indent + Math.max(...runs.map((r) => r.width))),
    h: head + rowH.reduce((a, b) => a + b, 0) + arrowH * (n - 1),
    over: Boolean(title && title.over) || runs.some((r) => r.over),
  };
}

function layout(ctx, slide, env) {
  const c = slide.content;
  const L = S.fit(slide.area, (k, aw, ah) => {
    const z = S.sizes(env, k);
    const first = aw / ah > 1.2;
    const a = measure(ctx, env, c, k, aw, first);
    if (!a.over && a.w <= aw && a.h <= ah) return { z, ...a };
    const b = measure(ctx, env, c, k, aw, !first);
    return !b.over && b.w <= aw && b.h <= ah ? { z, ...b } : { z, ...a };
  });
  L.texts = [];
  if (L.title) L.texts.push(S.textEntry(L.title, S.runBox(L.title, L.x, L.y)));
  L.nodes = [];
  if (L.horizontal) {
    const y = L.y + L.head;
    L.runs.forEach((r, i) => {
      const x = L.x + i * (L.colW + L.arrowW);
      const cx = x + L.colW / 2;
      L.nodes.push({ cx, cy: y + L.badge / 2, tx: cx, ty: y + L.badge + L.gap, align: "center", x0: x, x1: x + L.colW });
      L.texts.push(S.textEntry(r, S.runBox(r, cx, y + L.badge + L.gap, "center")));
    });
  } else {
    let y = L.y + L.head;
    L.runs.forEach((r, i) => {
      const cy = y + L.rowH[i] / 2;
      L.nodes.push({ cx: L.x + L.badge / 2, cy, tx: L.x + L.indent, ty: cy - r.height / 2, align: "left", y0: y, y1: y + L.rowH[i] });
      L.texts.push(S.textEntry(r, S.runBox(r, L.x + L.indent, cy - r.height / 2)));
      y += L.rowH[i] + L.arrowH;
    });
  }
  return L;
}

// Badge: an accent disc with the number in the plate colour when that reads (>= 3:1),
// else an accent ring with the number in the ink.
function badge(ctx, env, L, n, cx, cy, pal) {
  const r = L.badge / 2;
  const filled = (S.contrast(pal.plate, pal.accent) ?? 0) >= 3;
  if (filled) S.dot(ctx, cx, cy, r, pal.accent);
  else {
    ctx.beginPath();
    ctx.arc(cx, cy, r - L.z.body * 0.05, 0, Math.PI * 2);
    ctx.lineWidth = L.z.body * 0.1;
    ctx.strokeStyle = pal.accent;
    ctx.stroke();
  }
  const run = S.run(ctx, env, String(n), { px: L.z.body * 0.8, role: "heading", maxWidth: L.badge * 4, maxLines: 1 });
  S.drawRun(ctx, env, { ...run, lineHeight: L.badge }, cx, cy - L.badge / 2, "center", filled ? pal.plate : pal.ink);
}

function draw(ctx, t, slide, env, L) {
  const pal = S.palette(env.tokens);
  const { tokens } = env;
  if (L.title)
    S.enter(ctx, env, S.enterP(tokens, t - 0.1), L.z.heading, S.runBox(L.title, L.x, L.y), (c) =>
      S.drawRun(c, env, L.title, L.x, L.y, "left", pal.ink),
    );
  const stroke = Math.max(2, L.z.body * 0.07);
  L.nodes.forEach((nd, i) => {
    const p = S.enterP(tokens, t - env.times[i]);
    if (i > 0) {
      const prev = L.nodes[i - 1];
      const pad = L.badge * 0.2;
      const k = S.enterP(tokens, t - env.times[i] + 0.15);
      if (L.horizontal) S.arrow(ctx, prev.x1 + pad, nd.cy, nd.x0 - pad, nd.cy, stroke, pal.muted, k);
      else S.arrow(ctx, nd.cx, prev.y1 + pad, nd.cx, nd.y0 - pad, stroke, pal.muted, k);
    }
    const r = L.runs[i];
    const box = [Math.min(nd.cx - L.badge / 2, S.runBox(r, nd.tx, nd.ty, nd.align)[0]), nd.cy - L.badge / 2, Math.max(nd.cx + L.badge / 2, S.runBox(r, nd.tx, nd.ty, nd.align)[2]), Math.max(nd.cy + L.badge / 2, nd.ty + r.height)];
    S.enter(ctx, env, p, L.z.body, box, (c) => {
      badge(c, env, L, i + 1, nd.cx, nd.cy, pal);
      S.drawRun(c, env, r, nd.tx, nd.ty, nd.align, pal.ink);
    });
  });
}

module.exports = { name, steps, layout, draw };
