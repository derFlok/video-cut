// Caption: one phrase block (as given by the EDL, 1-2 lines) that stays on screen while the
// spoken word moves through it (karaoke), highlighted per tokens.captions.highlight
// (accent | underline | scale).
// Colours, fonts and motion come only from env.tokens.

const style = require("../lib/style");
const { step, brandSpring } = require("../lib/spring");
const { layoutBlock, drawBlock } = require("../lib/textblock");

const name = "caption";

const spoken = (item) => (item.words || []).filter((w) => String(w.w || "").trim());

// Placed once per group (the renderer calls this for every item, in time order).
function layout(ctx, item, env) {
  const { tokens } = env;
  const f = style.frame(env);
  const words = spoken(item);
  if (!words.length) return null;
  return env.once(item, () =>
    layoutBlock(
      ctx,
      env,
      words.map((w) => style.setCase(tokens, String(w.w).trim())),
      {
        px: style.captionPx(env),
        role: "heading",
        maxLines: 2,
        // Landscape, square and 4:5: bottom centre, above the bottom safe zone; on the
        // seam while a stacked slide_split view is on screen (any format).
        position: f.tall ? undefined : "bottom",
        seam: true,
        t0: item.t0,
        t1: item.t1,
      },
    ),
  );
}

function draw(ctx, t, item, env) {
  const { tokens } = env;
  const L = layout(ctx, item, env);
  if (!L) return null;
  const words = spoken(item);
  const hl = style.captionSettings(tokens).highlight;
  const { w, z } = brandSpring(tokens);
  const now = item.t0 + t; // word times are output time, like the group's
  return drawBlock(ctx, env, L, t, item.t1 - item.t0, (i) => {
    const word = words[i];
    const active = now >= word.t0 && now < word.t1;
    const k = active ? step(now - word.t0, w, z) : 0;
    return {
      highlight: (active && hl.accent) || !!word.emph,
      underline: hl.underline ? k : 0,
      scale: hl.scale ? k : 0,
    };
  });
}

module.exports = { name, layout, draw };
