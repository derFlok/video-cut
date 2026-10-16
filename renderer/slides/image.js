// Image: an uploaded page (content.asset, a picture inside the job folder) fitted into the
// area at its own aspect, letterboxed on the plate, with rounded corners and a gentle
// push-in over the whole time it is on screen. No build steps; its text is not read here.
// An uploaded page is untrusted and may show personal data: slide.redact (boxes
// [x0, y0, x1, y1], 0..1 of the image) are burnt into a private copy in the way of
// screenshot_frame (slide.redact_style "block" | "blur"), before any frame is drawn;
// frames only ever draw that copy. Anything but a list of boxes fails, never "no redaction".

const S = require("../lib/slide");
const style = require("../lib/style");
const card = require("../lib/card");
const shot = require("../scenes/screenshot_frame");

const name = "image";
const steps = () => 0;
const PUSH = 0.05; // the picture grows by this share over the slide's time
const PUSH_S = 8; // ... reaching it after this many seconds at most

const assets = (slide) => [{ ref: slide.content.asset, root: "job" }];

// The picture to draw: the page itself without redactions, else its redacted copy (made
// once per image and box list). Throws on malformed redactions.
const copies = new WeakMap();
function picture(slide, env, img) {
  const boxes = slide.redact;
  if (boxes == null) return img;
  if (!Array.isArray(boxes)) throw new Error("image: redact must be a list of [x0, y0, x1, y1] boxes");
  const norm = boxes.map((b, i) => shot.normBox(b, `redact[${i}]`));
  const mode = slide.redact_style == null ? "block" : slide.redact_style;
  if (!shot.STYLES.includes(mode)) throw new Error(`image: redact_style must be one of ${shot.STYLES.join(", ")}`);
  if (!norm.length) return img;
  if (typeof env.canvas !== "function") throw new Error("image: redaction needs env.canvas");
  const key = JSON.stringify([norm, mode]);
  const have = copies.get(img);
  if (have && have.key === key) return have.canvas;
  const { canvas } = shot.redacted(env, img, norm, card.palette(env.tokens).text, mode);
  copies.set(img, { key, canvas });
  return canvas;
}

function layout(ctx, slide, env) {
  const page = env.image(slide.content.asset);
  const img = page && page.width > 0 && page.height > 0 ? picture(slide, env, page) : page;
  const area = slide.area;
  const aw = area[2] - area[0];
  const ah = area[3] - area[1];
  if (!img || !(img.width > 0 && img.height > 0)) return { k: 1, fits: false, area, texts: [], missing: true, w: 0, h: 0 };
  const s = Math.min(aw / img.width, ah / img.height);
  const w = Math.round(img.width * s);
  const h = Math.round(img.height * s);
  const x = Math.round(area[0] + (aw - w) / 2);
  const y = Math.round(area[1] + (ah - h) / 2);
  return { k: 1, rank: w * h, fits: true, area, x, y, w, h, texts: [], box: [x, y, x + w, y + h], img };
}

const smooth = (u) => (u <= 0 ? 0 : u >= 1 ? 1 : u * u * (3 - 2 * u));

function draw(ctx, t, slide, env, L) {
  if (L.missing) return;
  const img = L.img;
  const p = S.enterP(env.tokens, t);
  const zoom = 1 + PUSH * smooth(t / Math.min(PUSH_S, Math.max(1, env.duration)));
  const r = style.radius(env.tokens, S.sizes(env, 1).body);
  S.enter(ctx, env, p, S.sizes(env, 1).body, L.box, (c) => {
    c.beginPath();
    c.roundRect(L.x, L.y, L.w, L.h, r);
    c.clip();
    const cx = L.x + L.w / 2;
    const cy = L.y + L.h / 2;
    c.drawImage(img, cx - (L.w * zoom) / 2, cy - (L.h * zoom) / 2, L.w * zoom, L.h * zoom);
  });
}

const staticFrom = (env) => Math.min(PUSH_S, Math.max(1, env.duration)) + 1;

module.exports = { name, steps, layout, draw, staticFrom, assets };
