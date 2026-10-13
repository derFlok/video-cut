// Chat bubble: a question shown as a message (props.text). props.from "viewer"
// (default) is an incoming message, tail bottom left, in the card colours;
// "speaker" is the reply, tail bottom right, in the inverted pair.
// Colours, fonts and motion come only from env.tokens.

const style = require("../lib/style");
const card = require("../lib/card");

const name = "chat_bubble";

const textOf = (item) => String((item.props && item.props.text) || "").trim();
const isSpeaker = (item) => String((item.props && item.props.from) || "").toLowerCase() === "speaker";

function layout(ctx, item, env) {
  if (!textOf(item)) return null;
  return env.once(item, () =>
    card.layoutCard(env, item, (k) => {
      const px = style.frame(env).unit * 0.058 * k;
      const padX = Math.round(px * 0.7);
      const padY = Math.round(px * 0.5);
      const tail = Math.round(px * 0.55);
      const maxW = card.maxCardWidth(env, px, padX) * 0.86;
      const text = card.textRun(ctx, env, textOf(item), { px, role: "body", maxWidth: maxW, maxLines: 5 });
      // The tail hangs below the bubble inside the laid-out box.
      return { px, padX, padY, tail, text, w: text.width + 2 * padX, h: text.height + 2 * padY + tail };
    }),
  );
}

function draw(ctx, t, item, env) {
  const L = layout(ctx, item, env);
  if (!L) return null;
  const { tokens } = env;
  const pal = card.palette(tokens);
  const speaker = isSpeaker(item);
  const fill = speaker ? pal.text : pal.fill;
  const ink = speaker ? pal.fill : pal.text;
  return card.drawCard(
    ctx,
    env,
    L,
    t,
    item.t1 - item.t0,
    (c, box) => {
      const [x0, y0, x1] = box;
      const y1 = box[3] - L.tail;
      const r = Math.min(L.px * 0.9, (y1 - y0) / 2);
      c.fillStyle = fill;
      c.beginPath();
      c.roundRect(x0, y0, x1 - x0, y1 - y0, r);
      c.fill();
      // Tail: a small wedge from the bottom edge towards the sender's side.
      const tx = speaker ? x1 - r * 0.9 : x0 + r * 0.9;
      const dir = speaker ? 1 : -1;
      c.beginPath();
      c.moveTo(tx - dir * L.tail * 0.2, y1 - 1);
      c.lineTo(tx + dir * L.tail * 0.9, y1 + L.tail);
      c.lineTo(tx + dir * L.tail * 1.2, y1 - 1);
      c.closePath();
      c.fill();
      card.drawRun(c, env, L.text, x0 + L.padX, y0 + L.padY, "left", ink);
    },
    { plate: false },
  );
}

module.exports = { name, layout, draw };
