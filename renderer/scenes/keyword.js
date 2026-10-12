// Keyword card: one big word or short phrase (props.text) in the brand caption style.
// Colours, fonts and motion come only from env.tokens.

const style = require("../lib/style");
const { layoutBlock, drawBlock } = require("../lib/textblock");

const name = "keyword";

const textOf = (item) => String((item.props && item.props.text) || "").trim();

// Placed once per card (the renderer calls this for every item, in time order).
function layout(ctx, item, env) {
  const { tokens, size } = env;
  const text = textOf(item);
  if (!text) return null;
  return env.once(item, () =>
    layoutBlock(ctx, env, style.setCase(tokens, text).split(/\s+/), {
      px: size[0] * 0.12,
      role: "display",
      maxLines: 3,
      position: "center",
      t0: item.t0,
      t1: item.t1,
    }),
  );
}

function draw(ctx, t, item, env) {
  const L = layout(ctx, item, env);
  if (!L) return null;
  const plain = { highlight: false, underline: 0, scale: 0 };
  return drawBlock(ctx, env, L, t, item.t1 - item.t0, () => plain);
}

module.exports = { name, layout, draw };
