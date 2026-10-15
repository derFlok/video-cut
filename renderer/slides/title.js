// Title slide: the title large and centred, a short accent rule growing under it, an
// optional subtitle in the muted ink. No build steps.

const S = require("../lib/slide");

const name = "title";
const steps = () => 0;

function layout(ctx, slide, env) {
  const c = slide.content;
  const L = S.fit(slide.area, (k, aw) => {
    const z = S.sizes(env, k);
    const title = S.run(ctx, env, c.title, { px: z.display, role: "display", maxWidth: aw, maxLines: 3 });
    const sub = c.subtitle ? S.run(ctx, env, c.subtitle, { px: z.body, role: "body", maxWidth: aw, maxLines: 3 }) : null;
    const rule = { w: Math.round(z.display * 1.4), h: Math.max(3, Math.round(z.display * 0.08)), gap: Math.round(z.display * 0.4) };
    return {
      z,
      title,
      sub,
      rule,
      w: Math.max(title.width, sub ? sub.width : 0, rule.w),
      h: title.height + 2 * rule.gap + rule.h + (sub ? sub.height : 0),
      over: title.over || Boolean(sub && sub.over),
    };
  });
  L.cx = Math.round((L.area[0] + L.area[2]) / 2);
  L.ruleY = L.y + L.title.height + L.rule.gap;
  L.subY = L.ruleY + L.rule.h + L.rule.gap;
  L.texts = [S.textEntry(L.title, S.runBox(L.title, L.cx, L.y, "center"))];
  if (L.sub) L.texts.push(S.textEntry(L.sub, S.runBox(L.sub, L.cx, L.subY, "center")));
  return L;
}

function draw(ctx, t, slide, env, L) {
  const pal = S.palette(env.tokens);
  const { tokens } = env;
  S.enter(ctx, env, S.enterP(tokens, t - 0.1), L.z.display, S.runBox(L.title, L.cx, L.y, "center"), (c) =>
    S.drawRun(c, env, L.title, L.cx, L.y, "center", pal.ink),
  );
  const g = S.enterP(tokens, t - 0.4);
  if (g > 0) S.roundRect(ctx, [L.cx - (L.rule.w * g) / 2, L.ruleY, L.cx + (L.rule.w * g) / 2, L.ruleY + L.rule.h], L.rule.h / 2, pal.accent);
  if (L.sub)
    S.enter(ctx, env, S.enterP(tokens, t - 0.6), L.z.body, S.runBox(L.sub, L.cx, L.subY, "center"), (c) =>
      S.drawRun(c, env, L.sub, L.cx, L.subY, "center", pal.muted),
    );
}

module.exports = { name, steps, layout, draw };
