// Call to action: the title large and centred, an optional subtitle in the muted ink and an
// optional address (content.url) in a rounded pill (the brand surface, else an accent
// outline). No build steps.

const S = require("../lib/slide");
const style = require("../lib/style");

const name = "cta";
const steps = () => 0;

function layout(ctx, slide, env) {
  const c = slide.content;
  const L = S.fit(slide.area, (k, aw) => {
    const z = S.sizes(env, k);
    const title = S.run(ctx, env, c.title, { px: z.display, role: "display", maxWidth: aw, maxLines: 3 });
    const sub = c.subtitle ? S.run(ctx, env, c.subtitle, { px: z.body, role: "body", maxWidth: aw, maxLines: 3 }) : null;
    const padX = Math.round(z.body * 0.8);
    const padY = Math.round(z.body * 0.4);
    const url = c.url ? S.run(ctx, env, c.url, { px: z.body, role: "heading", maxWidth: aw - 2 * padX, maxLines: 1 }) : null;
    const gap = Math.round(z.body * 0.8);
    const pill = url ? { w: url.width + 2 * padX, h: url.height + 2 * padY, padY } : null;
    return {
      z,
      title,
      sub,
      url,
      pill,
      gap,
      w: Math.max(title.width, sub ? sub.width : 0, pill ? pill.w : 0),
      h: title.height + (sub ? gap + sub.height : 0) + (pill ? gap * 1.4 + pill.h : 0),
      over: title.over || Boolean(sub && sub.over) || Boolean(url && url.over),
    };
  });
  L.cx = Math.round((L.area[0] + L.area[2]) / 2);
  L.subY = L.y + L.title.height + L.gap;
  L.pillY = Math.round((L.sub ? L.subY + L.sub.height : L.y + L.title.height) + L.gap * 1.4);
  L.texts = [S.textEntry(L.title, S.runBox(L.title, L.cx, L.y, "center"))];
  if (L.sub) L.texts.push(S.textEntry(L.sub, S.runBox(L.sub, L.cx, L.subY, "center")));
  if (L.url) L.texts.push(S.textEntry(L.url, S.runBox(L.url, L.cx, L.pillY + L.pill.padY, "center")));
  return L;
}

function draw(ctx, t, slide, env, L) {
  const pal = S.palette(env.tokens);
  const { tokens } = env;
  S.enter(ctx, env, S.enterP(tokens, t - 0.1), L.z.display, S.runBox(L.title, L.cx, L.y, "center"), (c) =>
    S.drawRun(c, env, L.title, L.cx, L.y, "center", pal.ink),
  );
  if (L.sub)
    S.enter(ctx, env, S.enterP(tokens, t - 0.45), L.z.body, S.runBox(L.sub, L.cx, L.subY, "center"), (c) =>
      S.drawRun(c, env, L.sub, L.cx, L.subY, "center", pal.muted),
    );
  if (L.url) {
    const box = [L.cx - L.pill.w / 2, L.pillY, L.cx + L.pill.w / 2, L.pillY + L.pill.h];
    S.enter(ctx, env, S.enterP(tokens, t - 0.8), L.z.body, box, (c) => {
      const r = Math.min(L.pill.h / 2, style.radius(tokens, L.pill.h));
      if (pal.surface) S.roundRect(c, box, r, pal.surface);
      else {
        c.beginPath();
        c.roundRect(box[0], box[1], box[2] - box[0], box[3] - box[1], r);
        c.lineWidth = Math.max(2, L.z.body * 0.06);
        c.strokeStyle = pal.accent;
        c.stroke();
      }
      S.drawRun(c, env, L.url, L.cx, L.pillY + L.pill.padY, "center", pal.ink);
    });
  }
}

module.exports = { name, steps, layout, draw };
