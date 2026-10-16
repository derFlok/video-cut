// Big number: one figure (content.value, e.g. "40 %", "3x", "12.000") very large in the
// accent (the ink when the accent does not reach 3:1 on the plate) and a label under it.
// With content.count and a numeric value it counts up calmly; it never runs past the value.

const S = require("../lib/slide");
const { parseNumber, formatNumber } = require("../scenes/number_card");

const name = "big_number";
const steps = () => 0;
const COUNT_FROM = 0.35; // s: the count starts when the figure has nearly arrived
const COUNT_S = 1.4; // s the count takes

function layout(ctx, slide, env) {
  const c = slide.content;
  const L = S.fit(slide.area, (k, aw) => {
    const z = S.sizes(env, k);
    const value = S.run(ctx, env, String(c.value), { px: z.big, role: "display", maxWidth: aw, maxLines: 1 });
    const label = S.run(ctx, env, c.label, { px: z.body, role: "body", maxWidth: Math.min(aw, env.unit * 1.3 * k), maxLines: 3 });
    const gap = Math.round(z.body * 0.5);
    return { z, value, label, gap, w: Math.max(value.width, label.width), h: value.height + gap + label.height, over: value.over || label.over };
  });
  L.cx = Math.round((L.area[0] + L.area[2]) / 2);
  L.labelY = L.y + L.value.height + L.gap;
  L.number = c.count ? parseNumber(String(c.value)) : null;
  L.texts = [S.textEntry(L.value, S.runBox(L.value, L.cx, L.y, "center")), S.textEntry(L.label, S.runBox(L.label, L.cx, L.labelY, "center"))];
  return L;
}

const smooth = (u) => (u <= 0 ? 0 : u >= 1 ? 1 : u * u * (3 - 2 * u));

function draw(ctx, t, slide, env, L) {
  const pal = S.palette(env.tokens);
  const { tokens } = env;
  const counting = slide.content.count === true && L.number && S.enterTime(tokens) > 0;
  const shown = counting ? formatNumber(L.number, L.number.value * smooth((t - COUNT_FROM) / COUNT_S)) : String(slide.content.value);
  const value = { ...L.value, rows: [{ text: shown, width: L.value.width }] };
  S.enter(ctx, env, S.enterP(tokens, t - 0.1), L.z.body * 2, S.runBox(L.value, L.cx, L.y, "center"), (c) =>
    S.drawRun(c, env, value, L.cx, L.y, "center", pal.accentText),
  );
  S.enter(ctx, env, S.enterP(tokens, t - 0.5), L.z.body, S.runBox(L.label, L.cx, L.labelY, "center"), (c) =>
    S.drawRun(c, env, L.label, L.cx, L.labelY, "center", pal.ink),
  );
}

const staticFrom = () => COUNT_FROM + COUNT_S + 0.6;

module.exports = { name, steps, layout, draw, staticFrom };
