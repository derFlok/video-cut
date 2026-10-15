// Quote: the text in the job language's quotation marks beside an accent bar, the author
// (optional) under it in the muted ink. No build steps.

const S = require("../lib/slide");

const name = "quote";
const steps = () => 0;

function layout(ctx, slide, env) {
  const c = slide.content;
  const L = S.fit(slide.area, (k, aw) => {
    const z = S.sizes(env, k);
    const bar = Math.max(4, Math.round(z.body * 0.14));
    const indent = bar + Math.round(z.body * 0.8);
    const blockW = Math.min(aw, env.unit * 1.45 * k);
    const text = S.run(ctx, env, S.quoted(c.text, env.lang), { px: z.heading, role: "heading", maxWidth: blockW - indent, maxLines: 6 });
    const author = c.author ? S.run(ctx, env, `— ${c.author}`, { px: z.body, role: "body", maxWidth: blockW - indent, maxLines: 2 }) : null;
    const gap = Math.round(z.body * 0.7);
    return {
      z,
      bar,
      indent,
      text,
      author,
      gap,
      w: indent + Math.max(text.width, author ? author.width : 0),
      h: text.height + (author ? gap + author.height : 0),
      over: text.over || Boolean(author && author.over),
    };
  });
  L.authorY = L.y + L.text.height + L.gap;
  L.texts = [S.textEntry(L.text, S.runBox(L.text, L.x + L.indent, L.y))];
  if (L.author) L.texts.push(S.textEntry(L.author, S.runBox(L.author, L.x + L.indent, L.authorY)));
  return L;
}

function draw(ctx, t, slide, env, L) {
  const pal = S.palette(env.tokens);
  const { tokens } = env;
  const g = S.enterP(tokens, t - 0.05);
  if (g > 0) S.roundRect(ctx, [L.x, L.y, L.x + L.bar, L.y + L.h * g], L.bar / 2, pal.accent);
  S.enter(ctx, env, S.enterP(tokens, t - 0.25), L.z.heading, S.runBox(L.text, L.x + L.indent, L.y), (c) =>
    S.drawRun(c, env, L.text, L.x + L.indent, L.y, "left", pal.ink),
  );
  if (L.author)
    S.enter(ctx, env, S.enterP(tokens, t - 0.8), L.z.body, S.runBox(L.author, L.x + L.indent, L.authorY), (c) =>
      S.drawRun(c, env, L.author, L.x + L.indent, L.authorY, "left", pal.muted),
    );
}

module.exports = { name, steps, layout, draw };
