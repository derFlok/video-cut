// Timeline: an optional title, then 2-6 points ({label, text}) on a line that grows from
// point to point: across where the area is wide, else down. Each point is one build step.

const S = require("../lib/slide");

const name = "timeline";
const steps = (c) => c.points.length;

function measure(ctx, env, c, k, aw, horizontal) {
  const z = S.sizes(env, k);
  const blockW = Math.min(aw, env.unit * 1.7 * k);
  const title = c.title ? S.run(ctx, env, c.title, { px: z.heading, role: "heading", maxWidth: blockW, maxLines: 2 }) : null;
  const head = title ? title.height + Math.round(z.heading * 0.8) : 0;
  const dotR = Math.round(z.body * 0.28);
  const n = c.points.length;
  const over = (rs) => Boolean(title && title.over) || rs.some((r) => r.over);
  if (horizontal) {
    const colW = Math.floor(blockW / n);
    const inner = Math.floor(colW * 0.9);
    const labels = c.points.map((p) => S.run(ctx, env, p.label, { px: z.body, role: "heading", maxWidth: inner, maxLines: 1 }));
    const bodies = c.points.map((p) => S.run(ctx, env, p.text, { px: z.label, role: "body", maxWidth: inner, maxLines: 4 }));
    const gap = Math.round(z.body * 0.5);
    const labelH = Math.max(...labels.map((r) => r.height));
    const textH = Math.max(...bodies.map((r) => r.height));
    return { z, horizontal, title, head, dotR, labels, bodies, colW, gap, labelH, w: blockW, h: head + labelH + gap + 2 * dotR + gap + textH, over: over([...labels, ...bodies]) };
  }
  const indent = Math.round(z.body * 1.3);
  const labels = c.points.map((p) => S.run(ctx, env, p.label, { px: z.body, role: "heading", maxWidth: blockW - indent, maxLines: 1 }));
  const bodies = c.points.map((p) => S.run(ctx, env, p.text, { px: z.label, role: "body", maxWidth: blockW - indent, maxLines: 2 }));
  const gap = Math.round(z.body * 0.55);
  const rowH = labels.map((l, i) => l.height + bodies[i].height);
  return {
    z,
    horizontal,
    title,
    head,
    dotR,
    labels,
    bodies,
    indent,
    gap,
    rowH,
    w: Math.max(title ? title.width : 0, indent + Math.max(...labels.map((r) => r.width), ...bodies.map((r) => r.width))),
    h: head + rowH.reduce((a, b) => a + b, 0) + gap * (n - 1),
    over: over([...labels, ...bodies]),
  };
}

function layout(ctx, slide, env) {
  const c = slide.content;
  const L = S.fit(slide.area, (k, aw, ah) => {
    const first = aw / ah > 1.2;
    const a = measure(ctx, env, c, k, aw, first);
    if (!a.over && a.w <= aw && a.h <= ah) return a;
    const b = measure(ctx, env, c, k, aw, !first);
    return !b.over && b.w <= aw && b.h <= ah ? b : a;
  });
  L.texts = [];
  if (L.title) L.texts.push(S.textEntry(L.title, S.runBox(L.title, L.x, L.y)));
  L.points = [];
  let y = L.y + L.head;
  L.labels.forEach((lab, i) => {
    if (L.horizontal) {
      const cx = L.x + (i + 0.5) * L.colW;
      const axis = y + L.labelH + L.gap + L.dotR;
      const ly = y + L.labelH - lab.height;
      const ty = axis + L.dotR + L.gap;
      L.points.push({ x: cx, y: axis, lx: cx, ly, tx: cx, ty, align: "center" });
    } else {
      const top = y;
      L.points.push({ x: L.x + L.dotR, y: top + lab.lineHeight / 2, lx: L.x + L.indent, ly: top, tx: L.x + L.indent, ty: top + lab.height, align: "left" });
      y += L.rowH[i] + L.gap;
    }
    const pt = L.points[i];
    L.texts.push(S.textEntry(lab, S.runBox(lab, pt.lx, pt.ly, pt.align)));
    L.texts.push(S.textEntry(L.bodies[i], S.runBox(L.bodies[i], pt.tx, pt.ty, pt.align)));
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
  const stroke = Math.max(2, L.z.body * 0.06);
  const first = L.points[0];
  L.points.forEach((pt, i) => {
    const start = env.times[i];
    if (i > 0) {
      const prev = L.points[i - 1];
      const k = S.enterP(tokens, t - start + 0.25);
      S.line(ctx, prev.x, prev.y, pt.x, pt.y, stroke, pal.muted, k);
    } else {
      // A short lead-in before the first point, so the line reads as a line from the start.
      const lead = L.horizontal ? [first.x - L.colW * 0.45, first.y] : [first.x, first.y - L.z.body * 0.6];
      S.line(ctx, lead[0], lead[1], first.x, first.y, stroke, pal.muted, S.enterP(tokens, t - start + 0.25));
    }
    const p = S.enterP(tokens, t - start);
    const lab = L.labels[i];
    const txt = L.bodies[i];
    const lb = S.runBox(lab, pt.lx, pt.ly, pt.align);
    const tb = S.runBox(txt, pt.tx, pt.ty, pt.align);
    S.enter(ctx, env, p, L.z.body, [Math.min(lb[0], tb[0], pt.x - L.dotR), Math.min(lb[1], pt.y - L.dotR), Math.max(lb[2], tb[2], pt.x + L.dotR), Math.max(tb[3], pt.y + L.dotR)], (c) => {
      S.dot(c, pt.x, pt.y, L.dotR, pal.accent);
      S.drawRun(c, env, lab, pt.lx, pt.ly, pt.align, pal.accentText);
      S.drawRun(c, env, txt, pt.tx, pt.ty, pt.align, pal.ink);
    });
  });
}

module.exports = { name, steps, layout, draw };
