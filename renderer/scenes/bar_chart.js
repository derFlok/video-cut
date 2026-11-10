// Bar chart: horizontal bars (props.bars: [{label, value}], up to 8), each growing on
// the brand spring one after the other, the value (with props.unit) at the bar's end.
// With a sparing accent (colors.accent_usage) only the largest bar takes the accent;
// the others are drawn in the text colour. Colours, fonts and motion come only from
// env.tokens.

const style = require("../lib/style");
const card = require("../lib/card");
const G = require("../lib/glass");

const name = "bar_chart";
const MAX_BARS = 8;

function barsOf(item) {
  const bars = (item.props && Array.isArray(item.props.bars) ? item.props.bars : [])
    .filter((b) => b && Number.isFinite(Number(b.value)))
    .slice(0, MAX_BARS)
    .map((b) => ({ label: String(b.label ?? "").trim(), value: Math.max(0, Number(b.value)) }));
  return bars;
}

const unitOf = (item) => String((item.props && item.props.unit) || "").trim();

// Value as written: whole numbers stay whole, others keep up to two decimals.
function valueText(v, unit, target = v) {
  // counting along: as many decimals as the target value has
  const d = Number.isInteger(target) ? 0 : Math.min(2, (String(Math.round(target * 100) / 100).split(".")[1] || "").length);
  const s = (Math.round(v * 10 ** d) / 10 ** d).toFixed(d);
  return unit ? `${s}${/^[%‰°]/.test(unit) ? "" : " "}${unit}` : s;
}

function layout(ctx, item, env) {
  const bars = barsOf(item);
  if (!bars.length) return null;
  return env.once(item, () => {
    const unit = style.frame(env).unit;
    return card.layoutCard(env, item, (k) => {
      const px = unit * 0.042 * k;
      const pad = Math.round(px * 0.8);
      const innerW = card.maxCardWidth(env, px, pad);
      const barH = Math.round(px * 0.8);
      const rowGap = Math.round(px * 0.55);
      const labels = bars.map((b) => card.textRun(ctx, env, b.label || " ", { px: px * 0.9, role: "label", maxWidth: innerW, maxLines: 1, minPx: px * 0.7 }));
      const values = bars.map((b) => card.textRun(ctx, env, valueText(b.value, unitOf(item)), { px, role: "heading", maxWidth: innerW * 0.3, maxLines: 1, minPx: px * 0.6 }));
      values.forEach((v, i) => (v.width = Math.max(v.width, card.tabularWidth(ctx, env, v, valueText(bars[i].value, unitOf(item))).w)));
      const valueW = Math.max(...values.map((v) => v.width));
      const labelGap = Math.round(px * 0.2);
      const rowH = Math.max(...labels.map((l) => l.height)) + labelGap + barH;
      return {
        px,
        pad,
        innerW,
        barH,
        rowGap,
        rowH,
        labelGap,
        labels,
        values,
        valueW,
        w: innerW + 2 * pad,
        h: bars.length * rowH + (bars.length - 1) * rowGap + 2 * pad,
      };
    });
  });
}

function draw(ctx, t, item, env) {
  const L = layout(ctx, item, env);
  if (!L) return null;
  const { tokens } = env;
  const pal = card.palette(tokens);
  const bars = barsOf(item);
  const max = Math.max(...bars.map((b) => b.value)) || 1;
  const top = bars.findIndex((b) => b.value === max);
  const dur = item.t1 - item.t0;
  const start = card.reachTime(tokens) * 0.6;
  const stagger = Math.min(0.12, (dur * 0.4) / bars.length);
  return card.drawCard(ctx, env, L, t, dur, (c, box) => {
    const x = box[0] + L.pad;
    const track = L.innerW - L.valueW - L.px * 0.4;
    const r = L.barH / 2;
    bars.forEach((b, i) => {
      const y = box[1] + L.pad + i * (L.rowH + L.rowGap);
      card.drawRun(c, env, L.labels[i], x, y, "left", pal.text);
      const by = y + L.labels[i].height + L.labelGap;
      const k = card.settle(tokens, t - start - i * stagger);
      const bw = Math.max(0, (track * b.value * k) / max);
      const base = c.globalAlpha;
      // the track the bar grows along: the text colour, faint, rounded like the bar
      c.globalAlpha = base * 0.1;
      c.fillStyle = pal.text;
      c.beginPath();
      c.roundRect(x, by, track, L.barH, r);
      c.fill();
      c.globalAlpha = base;
      // the bar: rounded caps, a gradient that lightens towards its head (the accent, or
      // the text colour for the other bars of a sparing brand)
      const hi = !(pal.sparing && i !== top);
      const ink = hi ? pal.accent : pal.text;
      if (bw > 0) {
        const w = Math.max(bw, L.barH);
        const g = c.createLinearGradient(x, 0, x + w, 0);
        if (g && g.addColorStop) {
          g.addColorStop(0, G.withAlpha(ink, hi ? 0.72 : 0.38));
          g.addColorStop(1, G.withAlpha(ink, hi ? 1 : 0.55));
          c.fillStyle = g;
        } else c.fillStyle = ink;
        c.beginPath();
        c.roundRect(x, by, w, L.barH, r);
        c.fill();
      }
      c.globalAlpha = base * k;
      card.drawTabular(c, env, L.values[i], valueText(b.value * k, unitOf(item), b.value), x + Math.max(bw, L.barH) + L.px * 0.3, by + (L.barH - L.values[i].height) / 2, "left", hi ? pal.accent : pal.text);
      c.globalAlpha = base;
    });
  });
}

module.exports = { name, layout, draw, valueText };
