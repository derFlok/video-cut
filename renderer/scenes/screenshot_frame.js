// Screenshot frame: an image from the job folder (props.file) fitted into a rounded,
// device-less frame; optional animated zoom to props.focus ([x0, y0, x1, y1], 0..1 of
// the image) and redaction boxes (props.redact: [[x0, y0, x1, y1], ...]).
// The renderer loads the file (only from inside the job folder) before the first
// frame. Redactions are burnt into a private copy of the image while the item is laid
// out, before any frame is drawn; frames only ever draw that copy, so no frame can
// show unredacted pixels. Colours, fonts and motion come only from env.tokens.

const style = require("../lib/style");
const card = require("../lib/card");

const name = "screenshot_frame";

// Longest side of the redacted copy (enough for a 4x zoom on a full-width frame).
const MAX_SIDE = 4096;
const MAX_ZOOM = 4;

const fileOf = (item) => String((item.props && item.props.file) || "").trim();

// Files the renderer has to load for this item ("job": only from inside the job folder).
function assets(item) {
  return fileOf(item) ? [{ ref: fileOf(item), root: "job" }] : [];
}

// A normalised box [x0, y0, x1, y1] clamped to 0..1; throws when it is not one.
function normBox(b, what) {
  const ok = Array.isArray(b) && b.length === 4 && b.every(Number.isFinite);
  const c = ok ? b.map((v) => Math.max(0, Math.min(1, v))) : null;
  if (!c || !(c[2] > c[0] && c[3] > c[1])) throw new Error(`${name}: ${what} must be [x0, y0, x1, y1] within 0..1`);
  return c;
}

// The image scaled to at most MAX_SIDE, with every redaction box cleared and filled
// solid. Clearing first means even a translucent token colour hides the pixels.
function redacted(env, img, boxes, color) {
  const s = Math.min(1, MAX_SIDE / Math.max(img.width, img.height));
  const w = Math.max(1, Math.round(img.width * s));
  const h = Math.max(1, Math.round(img.height * s));
  const cv = env.canvas(w, h);
  const c = cv.getContext("2d");
  c.drawImage(img, 0, 0, w, h);
  const grow = Math.max(2, Math.round(Math.max(w, h) * 0.004));
  c.globalAlpha = 1;
  c.fillStyle = color;
  for (const b of boxes) {
    const x0 = Math.max(0, Math.floor(b[0] * w) - grow);
    const y0 = Math.max(0, Math.floor(b[1] * h) - grow);
    const x1 = Math.min(w, Math.ceil(b[2] * w) + grow);
    const y1 = Math.min(h, Math.ceil(b[3] * h) + grow);
    c.clearRect(x0, y0, x1 - x0, y1 - y0);
    c.fillRect(x0, y0, x1 - x0, y1 - y0);
  }
  return { canvas: cv, w, h };
}

// Source rectangle (in the copy's pixels) for the zoom target: the focus box grown to
// the frame's aspect ratio, at most MAX_ZOOM, kept inside the image.
function focusRect(src, focus) {
  const ar = src.w / src.h;
  let fw = (focus[2] - focus[0]) * src.w;
  let fh = (focus[3] - focus[1]) * src.h;
  if (fw / fh < ar) fw = fh * ar;
  else fh = fw / ar;
  if (fw < src.w / MAX_ZOOM) [fw, fh] = [src.w / MAX_ZOOM, src.h / MAX_ZOOM];
  if (fw > src.w) [fw, fh] = [src.w, src.h];
  const cx = ((focus[0] + focus[2]) / 2) * src.w;
  const cy = ((focus[1] + focus[3]) / 2) * src.h;
  const x = Math.max(0, Math.min(src.w - fw, cx - fw / 2));
  const y = Math.max(0, Math.min(src.h - fh, cy - fh / 2));
  return [x, y, fw, fh];
}

function layout(ctx, item, env) {
  const file = fileOf(item);
  if (!file) return null;
  return env.once(item, () => {
    const img = env.image(file);
    if (!img || !(img.width > 0 && img.height > 0)) return null;
    const p = item.props || {};
    // Fail closed: anything but a list of boxes is an error, never "no redaction".
    if (p.redact != null && !Array.isArray(p.redact)) throw new Error(`${name}: redact must be a list of [x0, y0, x1, y1] boxes`);
    const boxes = (p.redact || []).map((b, i) => normBox(b, `redact[${i}]`));
    const focus = p.focus == null ? null : normBox(p.focus, "focus");
    const src = redacted(env, img, boxes, card.palette(env.tokens).text);
    const ar = src.w / src.h;
    const { size, safe } = env;
    const usableH = size[1] - ((safe && safe.top) || 0) - ((safe && safe.bottom) || 0);
    const L = card.layoutCard(env, item, (k) => {
      const px = size[0] * 0.05 * k;
      const pad = Math.round(px * 0.3);
      const iw = Math.min(card.maxCardWidth(env, px, pad), usableH * 0.42 * ar) * k;
      return { px, pad, iw, ih: iw / ar, w: iw + 2 * pad, h: iw / ar + 2 * pad };
    });
    return { ...L, src, focus: focus && focusRect(src, focus) };
  });
}

function draw(ctx, t, item, env) {
  const L = layout(ctx, item, env);
  if (!L) return null;
  const { tokens } = env;
  const dur = item.t1 - item.t0;
  const p = item.props || {};
  const zoomAt = Number.isFinite(p.zoom_at) ? p.zoom_at : Math.min(1.2, dur * 0.3);
  return card.drawCard(ctx, env, L, t, dur, (c, box) => {
    const x = box[0] + L.pad;
    const y = box[1] + L.pad;
    let sr = [0, 0, L.src.w, L.src.h];
    if (L.focus) {
      const k = card.settle(tokens, t - zoomAt);
      sr = sr.map((v, i) => v + (L.focus[i] - v) * k);
    }
    c.save();
    c.beginPath();
    c.roundRect(x, y, L.iw, L.ih, Math.max(0, style.radius(tokens, L.px) - L.pad));
    c.clip();
    c.drawImage(L.src.canvas, sr[0], sr[1], sr[2], sr[3], x, y, L.iw, L.ih);
    c.restore();
  });
}

module.exports = { name, assets, layout, draw, focusRect };
