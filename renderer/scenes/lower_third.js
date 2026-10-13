// Lower third: a name or topic (props.title) and an optional second line
// (props.subtitle) on a card in the lower part of the safe area, with an accent rule
// at its left edge that grows on the brand spring.
// Colours, fonts and motion come only from env.tokens.

const style = require("../lib/style");
const card = require("../lib/card");

const name = "lower_third";

const titleOf = (item) => String((item.props && item.props.title) || "").trim();
const subOf = (item) => String((item.props && item.props.subtitle) || "").trim();

function layout(ctx, item, env) {
  if (!titleOf(item)) return null;
  return env.once(item, () => {
    const { tokens } = env;
    const unit = style.frame(env).unit;
    return card.layoutCard(
      env,
      item,
      (k) => {
        const px = unit * 0.055 * k;
        const pad = Math.round(px * 0.55);
        const rule = Math.max(3, Math.round(px * 0.12));
        const ruleGap = Math.round(px * 0.45);
        const maxW = card.maxCardWidth(env, px, pad) - rule - ruleGap;
        const title = card.textRun(ctx, env, style.setCase(tokens, titleOf(item)), { px, role: "heading", maxWidth: maxW, maxLines: 2 });
        const sub = subOf(item) ? card.textRun(ctx, env, subOf(item), { px: px * 0.7, role: "body", maxWidth: maxW, maxLines: 2 }) : null;
        const between = sub ? Math.round(px * 0.15) : 0;
        return {
          px,
          pad,
          rule,
          ruleGap,
          title,
          sub,
          between,
          w: rule + ruleGap + Math.max(title.width, sub ? sub.width : 0) + 2 * pad,
          h: title.height + between + (sub ? sub.height : 0) + 2 * pad,
        };
      },
      // Landscape: bottom left of the area above the caption strip.
      { position: "lower", landscape: { position: "bottom", anchor: "left" } },
    );
  });
}

function draw(ctx, t, item, env) {
  const L = layout(ctx, item, env);
  if (!L) return null;
  const { tokens } = env;
  const pal = card.palette(tokens);
  return card.drawCard(ctx, env, L, t, item.t1 - item.t0, (c, box) => {
    const x = box[0] + L.pad;
    const y = box[1] + L.pad;
    const h = box[3] - box[1] - 2 * L.pad;
    const k = card.settle(tokens, t - card.reachTime(tokens) * 0.4);
    c.fillStyle = pal.accent;
    c.fillRect(x, y + (h * (1 - k)) / 2, L.rule, h * k);
    const tx = x + L.rule + L.ruleGap;
    card.drawRun(c, env, L.title, tx, y, "left", pal.text);
    if (L.sub) card.drawRun(c, env, L.sub, tx, y + L.title.height + L.between, "left", pal.muted);
  });
}

module.exports = { name, layout, draw };
