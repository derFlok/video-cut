// Scene library: every scene lays out clear of a synthetic face and inside the safe
// area, for every transition kind and a soft and a stiff spring; screenshot redaction
// is in place before the first visible frame; asset files stay inside the job folder.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { faceAt, faceDuring, overlapArea, allowedArea } = require("../lib/layout");
const { step } = require("../lib/spring");
const { union } = require("../lib/boxes");
const { readAsset, collectAssets } = require("../lib/assets");
const card = require("../lib/card");

const SCENES = path.join(__dirname, "..", "scenes");
const scene = (n) => require(path.join(SCENES, `${n}.js`));
const SIZE = [1080, 1920];
const SAFE = { top: 250, bottom: 350, side: 65 };
const FPS = 30;
const BRAND = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "brands", "_example", "brand.json"), "utf8"));

function tokensFor(transitions, spring) {
  const t = JSON.parse(JSON.stringify(BRAND));
  t.motion = { ...t.motion, transitions, spring };
  const c = t.colors;
  t.caption_colors = { text: c.text, box: c.surface, highlight: c.accent };
  return t;
}

// Canvas stand-in: every glyph is 0.55 em wide; method calls are logged.
function fakeCtx(log = []) {
  const state = { font: "10px x", globalAlpha: 1 };
  const own = {
    log,
    measureText(s) {
      const px = Number(/(\d+(?:\.\d+)?)px/.exec(this.font)[1]);
      return { width: String(s).length * px * 0.55 };
    },
  };
  return new Proxy(own, {
    get: (o, k) => (k in o ? o[k] : k in state ? state[k] : (...args) => log.push([k, ...args])),
    set: (o, k, v) => ((state[k] = v), true),
  });
}

function fakeCanvas(w, h) {
  const ctx = fakeCtx();
  return { width: w, height: h, ctx, getContext: () => ctx };
}

function makeEnv(tokens, faceBox, images = {}) {
  const samples = faceBox ? [{ t: 0, box: faceBox }, { t: 600, box: faceBox }] : [];
  const memo = new Map();
  const blocks = [];
  return {
    tokens,
    size: SIZE,
    safe: SAFE,
    fonts: { family: "x", ready: true },
    faceAt: (t) => faceAt(samples, t),
    faceDuring: (t0, t1) => faceDuring(samples, t0, t1),
    spring: step,
    once: (item, fn) => {
      if (!memo.has(item)) memo.set(item, fn());
      return memo.get(item);
    },
    blocksDuring: (t0, t1) => blocks.filter((b) => b.t0 < t1 && b.t1 > t0).map((b) => b.box),
    addBlock: (t0, t1, box) => blocks.push({ t0, t1, box }),
    image: (ref) => images[ref] || null,
    canvas: fakeCanvas,
  };
}

const SHOT = { width: 1600, height: 1000 };
const SAMPLES = {
  number_card: { value: "1,250 %", label: "of all requests answered on the same day" },
  list_item: { index: 2, text: "Write the hook before anything else", total: 3 },
  chat_bubble: { text: "How long does a first appointment usually take?", from: "viewer" },
  screenshot_frame: { file: "shot.png", focus: [0.6, 0.1, 0.9, 0.3], redact: [[0.1, 0.1, 0.4, 0.2]] },
  split_compare: { left: { title: "Myth", text: "You need expensive gear to start" }, right: { title: "Reality", text: "A phone and a window are enough" } },
  bar_chart: { bars: [{ label: "Before", value: 12 }, { label: "After", value: 31 }, { label: "Goal", value: 40 }], unit: "%" },
  lower_third: { title: "Topic of the day", subtitle: "Part 2 of 5" },
  cta_end: { text: "Send this to someone who needs it", logo: true },
  ai_badge: { text: "AI-generated" },
};

const FACES = { centre: [380, 780, 700, 1120], high: [400, 420, 680, 760], none: null };
const MOTION = [
  ["blur", { w: 13, z: 0.9 }],
  ["slide", { w: 11, z: 0.45 }],
  ["cut", { w: 22, z: 0.95 }],
];

// Draw an item frame by frame; the union of everything it reports drawing.
function drawAll(mod, item, env, ctx = fakeCtx()) {
  let box = null;
  for (let i = 0; i <= Math.round((item.t1 - item.t0) * FPS); i++) box = union(box, mod.draw(ctx, i / FPS, item, env));
  return box;
}

test("the sample covers every scene in renderer/scenes", () => {
  const names = fs.readdirSync(SCENES).filter((f) => f.endsWith(".js")).map((f) => f.slice(0, -3));
  for (const n of names) if (!["caption", "keyword"].includes(n)) assert.ok(n in SAMPLES, `no sample for ${n}`);
});

for (const [kind, spring] of MOTION)
  for (const [faceName, faceBox] of Object.entries(FACES))
    for (const [n, props] of Object.entries(SAMPLES))
      test(`${n} (${kind}, face ${faceName}): drawn box inside the frame, the safe area and clear of the face`, () => {
        const env = makeEnv(tokensFor(kind, spring), faceBox, { "shot.png": SHOT });
        const mod = scene(n);
        const item = { scene: n, t0: 1, t1: 4, props };
        const ctx = fakeCtx();
        const L = mod.layout(ctx, item, env);
        assert.ok(L && L.fits, "fits the safe area");
        assert.equal(L.overlapsFace, false);
        const box = drawAll(mod, item, env, ctx);
        assert.ok(box, "draws something");
        assert.ok(box[0] >= 0 && box[1] >= 0 && box[2] <= SIZE[0] && box[3] <= SIZE[1], `inside the frame: ${box}`);
        if (n === "cta_end") {
          // A full-frame plate replaces the video; only its content keeps the safe area.
          assert.deepEqual(box, [0, 0, SIZE[0], SIZE[1]]);
          return;
        }
        const [ax0, ay0, ax1, ay1] = allowedArea(SIZE, SAFE);
        assert.ok(box[0] >= ax0 && box[1] >= ay0 && box[2] <= ax1 && box[3] <= ay1, `inside the safe area: ${box}`);
        assert.equal(overlapArea(box, faceBox), 0, `clear of the face: ${box}`);
      });

test("scenes with nothing to show draw nothing", () => {
  const env = makeEnv(tokensFor("blur", { w: 13, z: 0.9 }), null);
  for (const n of Object.keys(SAMPLES).filter((n) => n !== "cta_end")) {
    const item = { scene: n, t0: 0, t1: 2, props: {} };
    assert.equal(drawAll(scene(n), item, env), null, n);
  }
});

test("blocks on screen together avoid each other", () => {
  const env = makeEnv(tokensFor("blur", { w: 13, z: 0.9 }), FACES.centre, { "shot.png": SHOT });
  const items = ["number_card", "lower_third"].map((n) => ({ scene: n, t0: 0, t1: 3, props: SAMPLES[n] }));
  const ctx = fakeCtx();
  const Ls = items.map((it) => scene(it.scene).layout(ctx, it, env));
  assert.ok(Ls.every((L) => !L.overlapsBlock && !L.overlapsFace));
  const [a, b] = items.map((it) => drawAll(scene(it.scene), it, env));
  assert.equal(overlapArea(a, b), 0);
});

test("screenshot redaction is burnt in before the first visible frame", () => {
  const env = makeEnv(tokensFor("blur", { w: 13, z: 0.9 }), FACES.centre, { "shot.png": SHOT });
  const mod = scene("screenshot_frame");
  const item = { scene: "screenshot_frame", t0: 0, t1: 3, props: SAMPLES.screenshot_frame };
  const main = fakeCtx();
  let first = null;
  for (let i = 0; i <= 3 * FPS && !first; i++) if (mod.draw(main, i / FPS, item, env)) first = i;
  assert.ok(first !== null);
  const L = mod.layout(main, item, env);
  const prepared = L.src.canvas;
  const ops = prepared.ctx.log;
  // The copy starts from the original image and the redaction follows right after.
  assert.equal(ops[0][0], "drawImage");
  assert.equal(ops[0][1], SHOT);
  const fills = ops.filter((o) => o[0] === "fillRect");
  const clears = ops.filter((o) => o[0] === "clearRect");
  assert.equal(fills.length, 1);
  assert.equal(clears.length, 1);
  assert.ok(ops.indexOf(clears[0]) < ops.indexOf(fills[0]));
  const [, x, y, w, h] = fills[0];
  const [b0, b1, b2, b3] = SAMPLES.screenshot_frame.redact[0];
  assert.ok(x <= b0 * L.src.w && y <= b1 * L.src.h && x + w >= b2 * L.src.w && y + h >= b3 * L.src.h);
  // Nothing drew the original before (the copy is ready at layout) and frames only use the copy.
  const images = main.log.filter((o) => o[0] === "drawImage");
  assert.ok(images.length > 0);
  assert.ok(images.every((o) => o[1] === prepared), "frames draw only the redacted copy");
});

test("screenshot blur draws the averaged cells over the fill, never the original", () => {
  const env = makeEnv(tokensFor("blur", { w: 13, z: 0.9 }), FACES.centre, { "shot.png": SHOT });
  const mod = scene("screenshot_frame");
  const props = { ...SAMPLES.screenshot_frame, redact_style: "blur" };
  const item = { scene: "screenshot_frame", t0: 0, t1: 3, props };
  const L = mod.layout(fakeCtx(), item, env);
  const ops = L.src.canvas.ctx.log;
  const fill = ops.findIndex((o) => o[0] === "fillRect");
  const clear = ops.findIndex((o) => o[0] === "clearRect");
  assert.ok(clear >= 0 && clear < fill, "cleared, then filled solid first");
  const draws = ops.filter((o) => o[0] === "drawImage");
  assert.equal(draws.length, 2); // the original copy, then the blurred cells
  const [, tiny, sx, sy, tw, th, x, y, w, h] = draws[1];
  assert.ok(ops.indexOf(draws[1]) > fill, "the blur goes over the fill");
  assert.notEqual(tiny, SHOT);
  assert.equal(tiny.width, tw);
  assert.equal(tiny.height, th);
  assert.deepEqual([sx, sy], [0, 0]);
  // the cells were averaged from the ORIGINAL image's box, a few px per cell
  const cell = mod.blurCell(SHOT, 1, w, h);
  assert.equal(tw, Math.ceil(w / cell));
  assert.equal(th, Math.ceil(h / cell));
  assert.ok(cell >= 13 && th <= 12, `cell ${cell}, ${tw}x${th}`);
  const [, src, ox, oy, ow, oh] = tiny.ctx.log.find((o) => o[0] === "drawImage");
  assert.equal(src, SHOT);
  assert.deepEqual([ox, oy, ow, oh], [x, y, w, h]); // copy scale 1: the same px
  // the default stays the solid block
  const plain = mod.layout(fakeCtx(), { ...item, props: SAMPLES.screenshot_frame }, env);
  assert.equal(plain.src.canvas.ctx.log.filter((o) => o[0] === "drawImage").length, 1);
});

test("an unknown redaction style fails instead of drawing nothing", () => {
  const env = makeEnv(tokensFor("blur", { w: 13, z: 0.9 }), null, { "shot.png": SHOT });
  const props = { ...SAMPLES.screenshot_frame, redact_style: "crop" };
  const item = { scene: "screenshot_frame", t0: 0, t1: 3, props };
  assert.throws(() => scene("screenshot_frame").layout(fakeCtx(), item, env), /redact_style/);
});

test("bad redaction boxes fail instead of being skipped", () => {
  const env = makeEnv(tokensFor("blur", { w: 13, z: 0.9 }), null, { "shot.png": SHOT });
  const item = { scene: "screenshot_frame", t0: 0, t1: 3, props: { file: "shot.png", redact: [[0.5, 0.5, 0.2, 0.9]] } };
  assert.throws(() => scene("screenshot_frame").layout(fakeCtx(), item, env), /redact\[0\]/);
  for (const redact of [[0.1, 0.1, 0.3, 0.2], { x0: 0.1 }, "all"]) {
    const it = { scene: "screenshot_frame", t0: 0, t1: 3, props: { file: "shot.png", redact } };
    assert.throws(() => scene("screenshot_frame").layout(fakeCtx(), it, env), /redact/, JSON.stringify(redact));
  }
});

test("zoom focus keeps the frame's aspect ratio, at most 4x, inside the image", () => {
  const { focusRect } = scene("screenshot_frame");
  const src = { w: 1600, h: 1000 };
  const [x, y, w, h] = focusRect(src, [0.9, 0.9, 0.95, 0.92]);
  assert.ok(Math.abs(w / h - 1.6) < 1e-9);
  assert.ok(w >= 400 - 1e-9 && x >= 0 && y >= 0 && x + w <= 1600 + 1e-9 && y + h <= 1000 + 1e-9);
  assert.deepEqual(focusRect(src, [0, 0, 1, 1]), [0, 0, 1600, 1000]);
});

test("number_card counts in the value's own format", () => {
  const { parseNumber, formatNumber } = scene("number_card");
  const n = parseNumber("1,250 %");
  assert.equal(n.value, 1250);
  assert.equal(formatNumber(n, 1250), "1,250 %");
  assert.equal(formatNumber(n, 0), "0 %");
  const d = parseNumber("3,5x");
  assert.equal(d.value, 3.5);
  assert.equal(formatNumber(d, 1.25), "1,3x");
  assert.equal(parseNumber("no number"), null);
  assert.equal(formatNumber(parseNumber("2026"), 2026), "2026");
});

test("settle is monotone and ends at 1 for soft, critical and stiff springs", () => {
  for (const spring of [{ w: 11, z: 0.45 }, { w: 13, z: 1 }, { w: 9, z: 1.6 }]) {
    const tokens = tokensFor("blur", spring);
    let prev = 0;
    for (let i = 0; i <= 300; i++) {
      const v = card.settle(tokens, i / 100);
      assert.ok(v >= prev - 1e-12 && v <= 1, `${JSON.stringify(spring)} at ${i / 100}: ${v}`);
      prev = v;
    }
    assert.equal(prev, 1);
  }
});

test("cta_end picks one logo variant by plate luminance and skips it when unsuitable", () => {
  const { logoRef } = scene("cta_end");
  const t = tokensFor("blur", { w: 13, z: 0.9 });
  t.logo = { light: "/b/light.svg", dark: "/b/dark.svg", placement: "outro" };
  const item = { props: { logo: true } };
  assert.equal(logoRef(item, t), "/b/light.svg"); // light bg plate
  assert.equal(logoRef({ props: { logo: true, plate: "dark" } }, t), "/b/dark.svg");
  assert.equal(logoRef({ props: {} }, t), null);
  assert.equal(logoRef(item, { ...t, logo: { ...t.logo, placement: "none" } }), null);
  assert.equal(logoRef(item, { ...t, logo: { dark: "/b/dark.svg", placement: "outro" } }), null);
});

test("cta_end draws the logo only on a fully opaque plate", () => {
  const t = tokensFor("blur", { w: 11, z: 0.45 });
  t.logo = { light: "/b/light.svg", placement: "outro" };
  const logo = { width: 400, height: 200 };
  const env = makeEnv(t, FACES.centre, { "/b/light.svg": logo });
  const mod = scene("cta_end");
  const item = { scene: "cta_end", t0: 0, t1: 3, props: { text: "Bye", logo: true } };
  let drew = 0;
  for (let i = 0; i <= 3 * FPS; i++) {
    const ctx = fakeCtx();
    mod.draw(ctx, i / FPS, item, env);
    const alphaAtPlate = ctx.log.find((o) => o[0] === "fillRect");
    const logoDrawn = ctx.log.some((o) => o[0] === "drawImage" && o[1] === logo);
    if (logoDrawn) {
      drew++;
      const tau = i / FPS;
      assert.equal(Math.min(card.settle(t, tau), card.settle(t, (3 - tau) * 2)), 1, `plate opaque at ${tau}`);
      assert.ok(alphaAtPlate);
    }
  }
  assert.ok(drew > 0);
});

test("assets: only files inside the job folder (brand files also from brands folders)", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "vc-assets-"));
  const job = path.join(tmp, "job");
  const brands = path.join(tmp, "brands");
  fs.mkdirSync(path.join(job, "input"), { recursive: true });
  fs.mkdirSync(path.join(brands, "b"), { recursive: true });
  fs.writeFileSync(path.join(job, "input", "a.png"), "x");
  fs.writeFileSync(path.join(tmp, "secret.png"), "x");
  fs.writeFileSync(path.join(brands, "b", "logo.svg"), "<svg/>");
  fs.writeFileSync(path.join(job, "input", "a.txt"), "x");
  fs.symlinkSync(path.join(tmp, "secret.png"), path.join(job, "input", "link.png"));
  const roots = { job, brands: [brands] };
  assert.equal(readAsset({ ref: "input/a.png", root: "job" }, roots).mime, "image/png");
  assert.equal(readAsset({ ref: `file://${path.join(job, "input", "a.png")}`, root: "job" }, roots).b64, "eA==");
  for (const ref of ["../secret.png", path.join(tmp, "secret.png"), "input/link.png", `file://${path.join(tmp, "secret.png")}`])
    assert.throws(() => readAsset({ ref, root: "job" }, roots), /outside the job folder/, ref);
  assert.throws(() => readAsset({ ref: "input/a.txt", root: "job" }, roots), /allowed image type/);
  assert.throws(() => readAsset({ ref: "https://example.org/a.png", root: "job" }, roots), /only files/);
  assert.throws(() => readAsset({ ref: "input/missing.png", root: "job" }, roots), /not found/);
  const logo = path.join(brands, "b", "logo.svg");
  assert.equal(readAsset({ ref: logo, root: "brand" }, roots).mime, "image/svg+xml");
  assert.throws(() => readAsset({ ref: logo, root: "job" }, roots), /outside the job folder/);
  const scenes = { screenshot_frame: scene("screenshot_frame") };
  const list = [{ scene: "screenshot_frame", props: { file: "input/a.png" } }, { scene: "screenshot_frame", props: { file: "input/a.png" } }];
  assert.equal(collectAssets(list, scenes, {}, roots).length, 1);
  fs.rmSync(tmp, { recursive: true, force: true });
});

test("a calm caption block (two lines, ~38 characters) fits the lower band of a close-up", () => {
  // Close-ups: the face box covers the centre band; the chest area below it is free.
  const closeUps = [[240, 420, 840, 1180], [190, 360, 890, 1240], [300, 520, 780, 1130]];
  const text = "die mir beim Laufen am Meer wirklich".split(" ");
  for (const face of closeUps)
    for (const [position, kase] of [["lower", "upper"], ["center", "normal"], ["center", "upper"]]) {
      const tokens = tokensFor("blur", { w: 13, z: 0.9 });
      tokens.captions = { ...tokens.captions, position, case: kase, style: "box" };
      const env = makeEnv(tokens, face);
      const item = { scene: "caption", t0: 0, t1: 1.5, words: text.map((w, i) => ({ w, t0: i * 0.2, t1: i * 0.2 + 0.2 })) };
      const ctx = fakeCtx();
      const L = scene("caption").layout(ctx, item, env);
      const msg = `${position}/${kase} face ${face}: ${L.box} (${L.lines.length} lines, ${L.px} px)`;
      assert.ok(L.fits && !L.overlapsFace, msg);
      assert.ok(L.lines.length <= 2, msg);
      assert.equal(L.band, "lower", msg);
      assert.ok(L.box[1] >= face[3], msg);
      const box = drawAll(scene("caption"), item, env, ctx);
      const [ax0, ay0, ax1, ay1] = allowedArea(SIZE, SAFE);
      assert.ok(box[0] >= ax0 && box[1] >= ay0 && box[2] <= ax1 && box[3] <= ay1, `inside the safe area: ${box} ${msg}`);
      assert.equal(overlapArea(box, face), 0, msg);
    }
});

test("a portrait screenshot uses the safe height, may cover the face, keeps caption room", () => {
  const tokens = tokensFor("blur", { w: 13, z: 0.9 });
  const mod = scene("screenshot_frame");
  const phone = { width: 1170, height: 2532 };
  const env = makeEnv(tokens, FACES.centre, { "phone.png": phone, "shot.png": SHOT });
  const tall = mod.layout(fakeCtx(), { scene: "screenshot_frame", t0: 0, t1: 3, props: { file: "phone.png" } }, env);
  const usable = SIZE[1] - SAFE.top - SAFE.bottom;
  const h = tall.box[3] - tall.box[1];
  assert.ok(tall.stage && h > usable * 0.6 && h <= usable * 0.8, `portrait card height ${h} of ${usable}`);
  assert.ok(tall.box[1] >= SAFE.top && tall.box[3] <= SIZE[1] - SAFE.bottom, "inside the safe area");
  assert.ok(tall.box[1] - SAFE.top < 60, `portrait card at the top: ${tall.box}`);
  assert.ok(mod.layoutFirst({ props: { file: "phone.png" } }, env) && !mod.layoutFirst({ props: { file: "shot.png" } }, env));
  // A two-line caption laid out after it (render.js lays layoutFirst items out first)
  // fits in the room left below, clear of the card.
  const text = "die mir beim Laufen am Meer wirklich".split(" ");
  const cap = { scene: "caption", t0: 0, t1: 1.5, words: text.map((w, i) => ({ w, t0: i * 0.2, t1: i * 0.2 + 0.2 })) };
  const C = scene("caption").layout(fakeCtx(), cap, env);
  assert.ok(C.fits && !C.overlapsBlock && C.box[1] >= tall.box[3], `caption ${C.box} below card ${tall.box}`);
  // Landscape stays a card clear of the face, as before.
  const env2 = makeEnv(tokens, FACES.centre, { "shot.png": SHOT });
  const wide = mod.layout(fakeCtx(), { scene: "screenshot_frame", t0: 0, t1: 3, props: { file: "shot.png" } }, env2);
  assert.ok(!wide.stage && !wide.overlapsFace);
});

test("ai_badge sits in the top corner opposite the logo, off the face, laid out first", () => {
  const badge = scene("ai_badge");
  const item = { scene: "ai_badge", t0: 1, t1: 3, props: { text: "AI-generated" } };
  const L = badge.layout(fakeCtx(), item, makeEnv(tokensFor("blur", { w: 13, z: 0.9 }), null));
  assert.equal(L.box[0], SAFE.side);
  assert.equal(L.box[1], SAFE.top);
  assert.equal(badge.layoutFirst(item), true);
  assert.equal(badge.top, true);
  assert.equal(badge.badge, true);
  // a logo in a left corner: the badge goes right
  const right = tokensFor("blur", { w: 13, z: 0.9 });
  right.logo = { ...right.logo, placement: "corner", corner: "top-left" };
  const R = badge.layout(fakeCtx(), { ...item }, makeEnv(right, null));
  assert.equal(R.box[2], SIZE[0] - SAFE.side);
  // the face in the top-left corner: the other corner
  const F = badge.layout(fakeCtx(), { ...item }, makeEnv(tokensFor("blur", { w: 13, z: 0.9 }), [0, 200, 500, 700]));
  assert.equal(F.box[2], SIZE[0] - SAFE.side);
  assert.equal(F.overlapsFace, false);
  // the face across the whole top: below it
  const W = badge.layout(fakeCtx(), { ...item }, makeEnv(tokensFor("blur", { w: 13, z: 0.9 }), [0, 200, 1080, 700]));
  assert.ok(W.box[1] > 700 && !W.overlapsFace);
  // drawn with tokens only: the surface fill and the text colour
  const log = [];
  badge.draw(fakeCtx(log), 0, { ...item }, makeEnv(tokensFor("blur", { w: 13, z: 0.9 }), null));
  assert.ok(log.some((c) => c[0] === "fillText" && c[1] === "AI-generated"));
});
