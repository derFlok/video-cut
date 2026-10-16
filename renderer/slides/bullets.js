// Bullet list: an optional title, then 2-5 items, each with an accent marker, building
// one by one (one build step per item). Left-aligned block, centred in the area.

const S = require("../lib/slide");

const name = "bullets";
const steps = (c) => c.items.length;

function layout(ctx, slide, env) {
  const c = slide.content;
  const L = S.fit(slide.area, (k, aw) => {
    const z = S.sizes(env, k);
    const blockW = Math.min(aw, env.unit * 1.5 * k);
    const indent = Math.round(z.body * 0.95);
    const title = c.title ? S.run(ctx, env, c.title, { px: z.heading, role: "heading", maxWidth: blockW, maxLines: 2 }) : null;
    const items = c.items.map((s) => S.run(ctx, env, s, { px: z.body, role: "body", maxWidth: blockW - indent, maxLines: 2 }));
    const gap = Math.round(z.body * 0.6);
    const head = title ? title.height + Math.round(z.heading * 0.7) : 0;
    const body = items.reduce((h, r) => h + r.height, 0) + gap * (items.length - 1);
    return {
      z,
      title,
      items,
      indent,
      gap,
      head,
      w: Math.max(title ? title.width : 0, ...items.map((r) => indent + r.width)),
      h: head + body,
      over: Boolean(title && title.over) || items.some((r) => r.over),
    };
  });
  L.rows = [];
  let y = L.y + L.head;
  for (const r of L.items) {
    L.rows.push(y);
    y += r.height + L.gap;
  }
  L.texts = [];
  if (L.title) L.texts.push(S.textEntry(L.title, S.runBox(L.title, L.x, L.y)));
  L.items.forEach((r, i) => L.texts.push(S.textEntry(r, S.runBox(r, L.x + L.indent, L.rows[i]))));
  return L;
}

function draw(ctx, t, slide, env, L) {
  const pal = S.palette(env.tokens);
  const { tokens } = env;
  if (L.title)
    S.enter(ctx, env, S.enterP(tokens, t - 0.1), L.z.heading, S.runBox(L.title, L.x, L.y), (c) =>
      S.drawRun(c, env, L.title, L.x, L.y, "left", pal.ink),
    );
  L.items.forEach((r, i) => {
    const y = L.rows[i];
    const p = S.enterP(tokens, t - env.times[i]);
    S.enter(ctx, env, p, L.z.body, [L.x, y, L.x + L.indent + r.width, y + r.height], (c) => {
      S.dot(c, L.x + L.z.body * 0.22, y + r.lineHeight / 2, L.z.body * 0.17, pal.accent);
      S.drawRun(c, env, r, L.x + L.indent, y, "left", pal.ink);
    });
  });
}

module.exports = { name, steps, layout, draw };
