// Number card: one big figure (props.value, e.g. "87 %", "1,200", "3.5x") counting up
// with the brand spring, and a short label under it (props.label).
// Colours, fonts and motion come only from env.tokens.

const style = require("../lib/style");
const card = require("../lib/card");

const name = "number_card";

// Leading prefix, the number, trailing suffix; null when there is no number to count.
// "1,200" groups thousands; a separator followed by other than three digits is decimal.
function parseNumber(s) {
  const m = /^(\D*?)(\d(?:[\d.,   ]*\d)?)(\D*)$/.exec(s);
  if (!m) return null;
  const num = m[2];
  const last = Math.max(num.lastIndexOf("."), num.lastIndexOf(","));
  let int = num;
  let frac = "";
  let dec = "";
  if (last >= 0 && num.length - last - 1 !== 3) {
    dec = num[last];
    int = num.slice(0, last);
    frac = num.slice(last + 1);
  }
  if (/[.,]/.test(frac)) return null;
  const group = (/[.,   ]/.exec(int) || [""])[0];
  const value = Number(`${int.replace(/[^\d]/g, "")}.${frac || "0"}`);
  if (!Number.isFinite(value)) return null;
  return { prefix: m[1], suffix: m[3], value, decimals: frac.length, dec, group };
}

function formatNumber(n, v) {
  const fixed = v.toFixed(n.decimals);
  const [i, f] = fixed.split(".");
  const int = n.group ? i.replace(/\B(?=(\d{3})+(?!\d))/g, n.group) : i;
  return `${n.prefix}${int}${f ? n.dec + f : ""}${n.suffix}`;
}

const valueOf = (item) => String((item.props && item.props.value) ?? "").trim();
const labelOf = (item) => String((item.props && item.props.label) || "").trim();

function layout(ctx, item, env) {
  const value = valueOf(item);
  if (!value) return null;
  return env.once(item, () => {
    const { tokens, size } = env;
    return card.layoutCard(env, item, (k) => {
      // px (the label size) drives padding and transition; the figure is four times it.
      const px = size[0] * 0.055 * k;
      const pad = Math.round(px * 0.9);
      const maxW = card.maxCardWidth(env, px, pad);
      const big = card.textRun(ctx, env, style.setCase(tokens, value), { px: px * 4, role: "display", maxWidth: maxW, maxLines: 1, minPx: px * 2 });
      const label = labelOf(item)
        ? card.textRun(ctx, env, labelOf(item), { px, role: "body", maxWidth: maxW, maxLines: 2 })
        : null;
      const gap = label ? Math.round(px * 0.2) : 0;
      return {
        px,
        pad,
        big,
        label,
        gap,
        w: Math.max(big.width, label ? label.width : 0) + 2 * pad,
        h: big.height + gap + (label ? label.height : 0) + 2 * pad,
      };
    });
  });
}

function draw(ctx, t, item, env) {
  const L = layout(ctx, item, env);
  if (!L) return null;
  const { tokens } = env;
  const pal = card.palette(tokens);
  const n = parseNumber(valueOf(item));
  return card.drawCard(ctx, env, L, t, item.t1 - item.t0, (c, box) => {
    const cx = (box[0] + box[2]) / 2;
    // Count up after the card has arrived, never past the target.
    const k = card.settle(tokens, t - card.reachTime(tokens) * 0.5);
    const shown = n ? formatNumber(n, n.value * k) : valueOf(item);
    const big = { ...L.big, rows: [{ text: style.setCase(tokens, shown), width: L.big.width }] };
    card.drawRun(c, env, big, cx, box[1] + L.pad, "center", pal.accent);
    if (L.label) card.drawRun(c, env, L.label, cx, box[1] + L.pad + L.big.height + L.gap, "center", pal.text);
  });
}

module.exports = { name, layout, draw, parseNumber, formatNumber };
