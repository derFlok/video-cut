// Screenshot frame: an image from the job folder (props.file) fitted into a rounded,
// device-less frame; optional animated zoom to props.focus ([x0, y0, x1, y1], 0..1 of
// the image) and redaction boxes (props.redact: [[x0, y0, x1, y1], ...]) drawn as
// props.redact_style: "block" (solid, the default) or "blur" (pixelated into cells at
// least the UI text height, then smoothed back up: the box keeps its colours, no text
// stays readable).
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
const LANDSCAPE_H = 0.42; // landscape image height, share of the usable height
const CAPTION_ROOM = 0.27; // portrait: share of the usable height left for a caption block
const STYLES = ["block", "blur"];
// Blur cells (image px): the UI text height (13 logical px, 2x on images with a long side
// >= HIDPI_MIN), or a quarter of the box's short side up to BLUR_MAX_TEXT text heights,
// as render's screen blur (_layouts.blur_cell).
const TEXT_PT = 13;
const HIDPI_MIN = 2700;
const BLUR_DIV = 4;
const BLUR_MAX_TEXT = 3;

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
// solid. Clearing first means even a translucent token colour hides the pixels. "blur"
// then draws the box's cells (an area-averaged copy, a few px per cell) smoothed back
// up over that fill: what shows is the blur, and the fill stays if drawing it fails.
function redacted(env, img, boxes, color, mode = "block") {
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
    if (mode === "blur") blurBox(env, c, img, s, [x0, y0, x1, y1]);
  }
  return { canvas: cv, w, h };
}

// Cell size (copy px) of a blurred box bw x bh in a copy scaled by s from `img`.
function blurCell(img, s, bw, bh) {
  const text = TEXT_PT * (Math.max(img.width, img.height) >= HIDPI_MIN ? 2 : 1) * s;
  return Math.max(2, Math.ceil(Math.max(text, Math.min(Math.min(bw, bh) / BLUR_DIV, BLUR_MAX_TEXT * text))));
}

// Box r = [x0, y0, x1, y1] (copy px) of the copy drawn as its blurred original: the
// original's pixels averaged into one px per cell, drawn back up with smoothing.
function blurBox(env, c, img, s, r) {
  const [x0, y0, x1, y1] = r;
  const bw = x1 - x0;
  const bh = y1 - y0;
  const cell = blurCell(img, s, bw, bh);
  const tw = Math.max(1, Math.ceil(bw / cell));
  const th = Math.max(1, Math.ceil(bh / cell));
  const tiny = env.canvas(tw, th);
  const t = tiny.getContext("2d");
  t.imageSmoothingEnabled = true;
  t.imageSmoothingQuality = "high";
  t.drawImage(img, x0 / s, y0 / s, bw / s, bh / s, 0, 0, tw, th);
  c.imageSmoothingEnabled = true;
  c.imageSmoothingQuality = "high";
  c.drawImage(tiny, 0, 0, tw, th, x0, y0, bw, bh);
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

// Whether the item must be laid out before the time-ordered rest: a portrait screenshot,
// and any screenshot in a landscape frame (it is the picture of the moment there).
function layoutFirst(item, env) {
  const file = fileOf(item);
  const img = file && env.image(file);
  if (!(img && img.width > 0 && img.height > 0)) return false;
  return img.width < img.height || style.frame(env).landscape;
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
    const mode = p.redact_style == null ? "block" : p.redact_style;
    if (!STYLES.includes(mode)) throw new Error(`${name}: redact_style must be one of ${STYLES.join(", ")}`);
    const focus = p.focus == null ? null : normBox(p.focus, "focus");
    const src = redacted(env, img, boxes, card.palette(env.tokens).text, mode);
    const ar = src.w / src.h;
    const { safe } = env;
    const f = style.frame(env);
    const usableH = f.h - ((safe && safe.top) || 0) - ((safe && safe.bottom) || 0);
    // Landscape: a card of at most LANDSCAPE_H of the usable height, clear of the face.
    // Portrait (a phone screenshot): as tall as the safe area allows, at its top, minus
    // room for one caption block below it; it is the picture of the moment and may cover
    // the speaker (stage). It is laid out before the captions (layoutFirst), so they take
    // the room below instead of pushing it aside.
    // Landscape frame: the screenshot is the picture of the moment (stage), fitted into the
    // area above the caption strip keeping its aspect: a landscape screenshot fills it, a
    // portrait one stands at the right edge with the side space left free.
    const portrait = ar < 1;
    const wideFrame = f.landscape;
    const stage = portrait || wideFrame;
    const reserve = style.captionReserve(env);
    const L = card.layoutCard(
      env,
      item,
      (k) => {
        const px = f.unit * 0.05 * k;
        const pad = Math.round(px * 0.3);
        const reach = style.bleed(px);
        const maxH = wideFrame
          ? usableH - reserve - 2 * pad - 2 * reach
          : portrait
            ? usableH * (1 - CAPTION_ROOM) - 2 * pad - 2 * reach
            : usableH * LANDSCAPE_H;
        const room = wideFrame ? f.w - 2 * ((safe && safe.side) || 0) - 2 * reach - 2 * pad : card.maxCardWidth(env, px, pad);
        const iw = Math.min(room, maxH * ar) * k;
        return { px, pad, iw, ih: iw / ar, w: iw + 2 * pad, h: iw / ar + 2 * pad };
      },
      {
        stage,
        position: portrait && !wideFrame ? "top" : "center",
        landscape: { position: "center", anchor: portrait ? "right" : "center" },
      },
    );
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

module.exports = { name, assets, layout, layoutFirst, draw, focusRect, blurCell };
