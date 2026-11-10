// Glass material: the vibrancy guard, the accent lift, deterministic grain and backdrop,
// the material resolution of the canvas scenes, and the glass mask of both renderers.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const G = require("../lib/glass");
const material = require("../lib/material");

const ROOT = path.join(__dirname, "..");
const BROWSERS = process.env.PLAYWRIGHT_BROWSERS_PATH || path.join(ROOT, "..", ".tools", "ms-playwright");
const haveBrowser = fs.existsSync(BROWSERS);

test("tint: the material's own alpha when the ink keeps 4.5:1 over the measured frost", () => {
  const t = G.tint("#1A1F27", "#F2F4F7", 0.48, 4.5, [0.05, 0.12]);
  assert.equal(t.alpha, 0.48);
  assert.equal(t.densified, false);
  assert.ok(t.contrast >= 4.5);
});

test("tint: a lighter frost (or none measured: black..white) makes the glass denser and says so", () => {
  const measured = G.tint("#1A1F27", "#F2F4F7", 0.48, 4.5, [0.05, 0.6]);
  const unknown = G.tint("#1A1F27", "#F2F4F7", 0.48, 4.5, null);
  assert.ok(measured.densified && measured.alpha > 0.48);
  assert.ok(unknown.alpha >= measured.alpha);
  for (const t of [measured, unknown]) {
    assert.ok(G.worst("#1A1F27", "#F2F4F7", t.alpha, t === unknown ? null : [0.05, 0.6]).ratio >= 4.5);
  }
});

test("span: anything but a valid [lmin, lmax] is the full range", () => {
  assert.deepEqual(G.span([0.1, 0.4]), [0.1, 0.4]);
  for (const bad of [null, [0.5, 0.1], [0, 2], "x", [0.1]]) assert.deepEqual(G.span(bad), [0, 1]);
});

test("accentOn: the accent is lifted until it keeps 3:1 over the worst frost", () => {
  const a = G.accentOn("#1A1F27", "#5B8CFF", 0.5, [0.05, 0.6]);
  assert.notEqual(a, "#5B8CFF");
  assert.ok(G.worst("#1A1F27", a, 0.5, [0.05, 0.6]).ratio >= G.LARGE_CONTRAST);
  // already readable: unchanged
  assert.equal(G.accentOn("#FFFFFF", "#101010", 0.6, [0.2, 0.9]), "#101010");
});

test("grain and backdrop are deterministic pixels", () => {
  assert.deepEqual(G.grain(16).data, G.grain(16).data);
  const a = G.backdrop(40, 70);
  assert.equal(a.data.length, 40 * 70 * 4);
  assert.deepEqual(a.data, G.backdrop(40, 70).data);
  const [lo, hi] = G.backdropRange(1080, 1920);
  assert.ok(lo >= 0 && hi <= 1 && lo < hi);
});

test("frost: sigma follows the short side", () => {
  assert.equal(G.frost(1080, "dark").sigma, Math.round(G.FROST_SIGMA * 1080 * 10) / 10);
  assert.ok(G.frost(1080, "light").brightness > 0 && G.frost(1080, "dark").brightness < 0);
});

test("polarity: a bright backdrop makes light glass, a dark one dark glass, the brand theme decides without one", () => {
  assert.equal(G.polarity([0.05, 0.9, 0.55], "dark"), "light");
  assert.equal(G.polarity([0.01, 0.4, 0.06], "light"), "dark");
  assert.equal(G.polarity(null, "light"), "light");
  assert.equal(G.polarity(null, "dark"), "dark");
});

test("frost: the luminance is compressed towards the backdrop's mean and lifted (light) or lowered (dark)", () => {
  const range = [0.03, 0.85, 0.5];
  const light = G.frost(1080, "light", range);
  const fr = G.frostRange(range, light);
  assert.ok(fr[1] - fr[0] < range[1] - range[0]); // calmer
  assert.ok(fr[0] > range[0]); // dark hair under light glass is lifted
  const dark = G.frost(1080, "dark", [0.01, 0.3, 0.05]);
  assert.ok(G.frostRange([0.01, 0.3, 0.05], dark)[1] < 0.3);
  // without a measurement the frost still cannot reach black and white
  const none = G.frostRange(null, G.frost(1080, "light"));
  assert.ok(none[0] > 0 && none[1] <= 1);
});

test("material: over a bright picture a dark brand gets light glass with its dark colour as ink, no densifying", () => {
  const m = material.resolve(TOK("glass", { glass_backdrop: [0.04, 0.86, 0.52] }));
  assert.equal(m.theme, "light");
  assert.ok(G.lum(G.chan(m.fill)) > 0.8 && G.lum(G.chan(m.text)) < 0.02);
  assert.equal(m.tint.densified, false);
  assert.ok(m.tint.contrast >= 4.5);
  assert.ok(m.tint.alpha <= 0.45);
  const style = require("../lib/style");
  assert.equal(style.captionColors(TOK("glass", { glass_backdrop: [0.04, 0.86, 0.52] })).text, m.text);
});

test("glass plates draw continuous-curvature corners (capsules stay round)", () => {
  const ops = [];
  const ctx = new Proxy({}, { get: (_, p) => (p === "getTransform" ? () => null : (...a) => ops.push(p)) });
  const env = { tokens: TOK("glass", { glass_backdrop: [0.05, 0.6, 0.3] }), size: [1080, 1920] };
  material.plate(ctx, env, (c) => { c.beginPath(); c.roundRect(10, 10, 400, 200, 40); }, { px: 40 });
  assert.ok(ops.includes("bezierCurveTo") && !ops.includes("roundRect"));
  ops.length = 0;
  material.plate(ctx, env, (c) => { c.beginPath(); c.roundRect(10, 10, 400, 80, 40); }, { px: 40 });
  assert.ok(ops.includes("roundRect"));
});

const TOK = (depth, extra = {}) => ({
  colors: { bg: "#0E1116", surface: "#1A1F27", text: "#F2F4F7", muted: "#9AA3AF", accent: "#5B8CFF" },
  caption_colors: { box: "#1A1F27", text: "#F2F4F7", highlight: "#5B8CFF" },
  motion: depth ? { depth } : {},
  ...extra,
});

test("material: depth defaults to glow on a dark plate and soft on a light one; glass is opt-in", () => {
  assert.equal(material.resolve(TOK(null)).depth, "glow");
  const light = TOK(null);
  light.caption_colors = { box: "#FFFFFF", text: "#141414", highlight: "#141414" };
  assert.equal(material.resolve(light).depth, "soft");
  const g = material.resolve(TOK("glass", { glass_backdrop: [0.05, 0.5] }));
  assert.equal(g.depth, "glass");
  assert.ok(g.tint.alpha >= G.TINT[g.theme]);
  assert.deepEqual(material.report(TOK("glass", { glass_backdrop: [0.05, 0.5] })).backdrop, [0.05, 0.5]);
});

test("material: on glass the caption highlight is the lifted accent", () => {
  const style = require("../lib/style");
  const t = TOK("glass", { glass_backdrop: [0.05, 0.6] });
  const hl = style.captionColors(t).highlight;
  assert.equal(hl, material.resolve(t).accent);
  assert.notEqual(hl, "#5B8CFF");
  assert.equal(style.captionColors(TOK("soft")).highlight, "#5B8CFF");
});

// --- the masks ----------------------------------------------------------------------------

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "vc-glass-"));
const probeFrames = (f) =>
  Number(execFileSync("ffprobe", ["-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", f], { encoding: "utf8" }).trim());
// mean gray of a mask frame (0..255)
const meanAt = (f, n) =>
  Number(
    /YAVG=([\d.]+)/.exec(
      execFileSync("ffmpeg", ["-v", "error", "-i", f, "-vf", `select=eq(n\\,${n}),signalstats,metadata=print:file=-`, "-frames:v", "1", "-f", "null", "-"], { encoding: "utf8" }),
    )[1],
  );

test("overlay renderer: a glass brand writes a mask clip (frames = overlay frames) and its frost json", { skip: !haveBrowser && "no browser" }, () => {
  const dir = tmp();
  const spec = {
    size: [360, 640], fps: 10, duration: 1.5, safe: {}, face: [],
    tokens: { ...TOK("glass"), fonts: { weights: [400, 700], files: {} }, captions: { style: "box" } },
    captions: [{ t0: 0.2, t1: 1.3, words: [{ w: "Glass", t0: 0.2, t1: 0.6 }, { w: "test", t0: 0.6, t1: 1.0 }] }],
    overlays: [],
  };
  fs.writeFileSync(path.join(dir, "o.json"), JSON.stringify(spec));
  const out = execFileSync(process.execPath, [path.join(ROOT, "render.js"), path.join(dir, "o.json"), "--out", path.join(dir, "o.mov"), "--glass", path.join(dir, "g.mkv")], {
    env: { ...process.env, PLAYWRIGHT_BROWSERS_PATH: BROWSERS }, encoding: "utf8",
  });
  const s = JSON.parse(out.trim().split("\n").pop());
  assert.ok(s.ok && s.glass && s.glass.masked_frames > 0, JSON.stringify(s));
  assert.equal(probeFrames(path.join(dir, "g.mkv")), probeFrames(path.join(dir, "o.mov")));
  const p = JSON.parse(fs.readFileSync(path.join(dir, "g.mkv.json"), "utf8"));
  assert.ok(p.sigma > 0 && p.saturation > 1);
  assert.equal(meanAt(path.join(dir, "g.mkv"), 0), 0); // before the caption: no glass
  assert.ok(meanAt(path.join(dir, "g.mkv"), 8) > 1); // the caption box is glass
});

test("overlay renderer: no mask (and a stale one removed) for a brand without glass", { skip: !haveBrowser && "no browser" }, () => {
  const dir = tmp();
  fs.writeFileSync(path.join(dir, "g.mkv"), "stale");
  const spec = { size: [360, 640], fps: 10, duration: 0.5, safe: {}, face: [], tokens: { ...TOK("soft"), fonts: { weights: [400, 700], files: {} } }, captions: [], overlays: [] };
  fs.writeFileSync(path.join(dir, "o.json"), JSON.stringify(spec));
  execFileSync(process.execPath, [path.join(ROOT, "render.js"), path.join(dir, "o.json"), "--out", path.join(dir, "o.mov"), "--glass", path.join(dir, "g.mkv")], {
    env: { ...process.env, PLAYWRIGHT_BROWSERS_PATH: BROWSERS }, encoding: "utf8",
  });
  assert.ok(!fs.existsSync(path.join(dir, "g.mkv")));
});

test("motion renderer: the mask pass shows only glass surfaces, frame for frame with the clip", { skip: !haveBrowser && "no browser" }, () => {
  const dir = tmp();
  fs.writeFileSync(path.join(dir, "scene.js"), `export default function scene(vc) { vc.c.stat(vc.c.slot(vc.stage), { value: 7, at: 0.1, label: "x" }); }\n`);
  const spec = {
    mode: "motion", size: [540, 960], fps: 10, duration: 1, unit: 540, region: [30, 100, 510, 400], scene: "scene.js", seed: 1, words: [], beats: {},
    tokens: { ...TOK("glass", { glass_backdrop: [0.05, 0.4] }), fonts: { weights: [400, 800], files: {} } },
  };
  fs.writeFileSync(path.join(dir, "m.json"), JSON.stringify(spec));
  const out = execFileSync(process.execPath, [path.join(ROOT, "motion.js"), path.join(dir, "m.json"), "--region-only", "--out", path.join(dir, "c.mov"), "--mask", path.join(dir, "g.mkv")], {
    env: { ...process.env, VIDEOCUT_JOB_DIR: dir, PLAYWRIGHT_BROWSERS_PATH: BROWSERS }, encoding: "utf8",
  });
  const s = JSON.parse(out.trim().split("\n").pop());
  assert.ok(s.ok, JSON.stringify(s));
  assert.equal(s.style.depth, "glass");
  assert.ok(s.glass.tint_alpha >= G.TINT.dark && s.glass.sigma > 0 && s.glass.theme === "dark");
  assert.equal(probeFrames(path.join(dir, "g.mkv")), s.frames);
  assert.ok(meanAt(path.join(dir, "g.mkv"), 9) > meanAt(path.join(dir, "g.mkv"), 0));
});

test("plain captions on a glass brand keep the brand caption colours (not the glass pair); cards still take it", () => {
  const style = require("../lib/style");
  const bright = { glass_backdrop: [0.04, 0.86, 0.52] }; // light glass for the panels
  const plain = { ...TOK("glass", bright), captions: { style: "plain" } };
  const box = { ...TOK("glass", bright), captions: { style: "box" } };
  const m = material.resolve(plain);
  assert.equal(m.theme, "light");
  assert.equal(style.captionColors(plain).text, "#F2F4F7"); // the brand caption text
  assert.equal(style.captionColors(plain).box, "#1A1F27"); // its halo colour
  assert.equal(style.captionColors(box).text, material.resolve(box).text); // on glass: the material
  const card = require("../lib/card");
  assert.equal(card.palette(plain).text, m.text); // a card is always a glass plate
  assert.equal(card.palette(plain).fill, m.fill);
});

test("a glass brand's plain caption over a dark picture keeps 4.5:1 with its halo (real renderer)", { skip: !haveBrowser && "no browser" }, () => {
  const dir = tmp();
  const spec = {
    size: [360, 640], fps: 10, duration: 1.2, safe: {}, face: [],
    tokens: { ...TOK("glass", { glass_backdrop: [0.04, 0.86, 0.52] }), fonts: { weights: [400, 700], files: {} }, captions: { style: "plain" } },
    captions: [{ t0: 0.1, t1: 1.1, words: [{ w: "Dunkles", t0: 0.1, t1: 0.5 }, { w: "Hemd", t0: 0.5, t1: 1.0 }] }],
    overlays: [],
  };
  fs.writeFileSync(path.join(dir, "o.json"), JSON.stringify(spec));
  execFileSync(process.execPath, [path.join(ROOT, "render.js"), path.join(dir, "o.json"), "--still", "0.9", path.join(dir, "s.png"), "--boxes", path.join(dir, "b.json")], {
    env: { ...process.env, PLAYWRIGHT_BROWSERS_PATH: BROWSERS }, encoding: "utf8",
  });
  // over a dark grey picture (a dark shirt): the text's luminance against the darkest
  // drawn surround (the halo or the picture itself)
  const raw = execFileSync("ffmpeg", ["-v", "error", "-f", "lavfi", "-i", "color=c=0x262626:s=360x640:d=1", "-i", path.join(dir, "s.png"),
    "-filter_complex", "[0:v][1:v]overlay=format=auto", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]);
  const boxes = JSON.parse(fs.readFileSync(path.join(dir, "b.json"), "utf8"));
  const cap = (Array.isArray(boxes) ? boxes : boxes.boxes).find((b) => b.scene === "caption").box;
  const ls = [];
  for (let y = Math.max(0, cap[1]); y < Math.min(640, cap[3]); y++)
    for (let x = Math.max(0, cap[0]); x < Math.min(360, cap[2]); x++) {
      const i = (y * 360 + x) * 3;
      ls.push(G.lum([raw[i], raw[i + 1], raw[i + 2]]));
    }
  ls.sort((a, b) => a - b);
  const text = ls[Math.floor(ls.length * 0.99)];
  const around = ls[Math.floor(ls.length * 0.5)];
  assert.ok((text + 0.05) / (around + 0.05) >= 4.5, `text ${text} vs ${around}`);
});
