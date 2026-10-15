#!/usr/bin/env node
// Transparent overlay renderer (BRIEF §8): one headless Chromium page with a canvas,
// a pure draw(t) per frame, frames piped as PNG to ffmpeg -> ProRes 4444 with alpha.
//
//   node render.js <overlays.json> [--out <overlay.mov>] [--boxes <boxes.json>]
//                  [--still <t> <png>] [--sheet <jpg>] [--blur <subframes>]
//   node render.js <slide.json> [--out <slide.mov>] [--still <t> <png>] [--report <json>]
//
// A spec with "mode": "slide" is one slide (renderer/slides/<template>.js) as an OPAQUE
// clip on the brand background (ProRes 422 HQ, BT.709): "size" is the clip (the slide's
// part of the frame), "unit" the short side of the output frame (the type scale),
// "slide" {template, content, area (content box in clip px), reveal (clip seconds)}.
// --still draws the whole output frame when the spec has "frame" {size, rect, speaker}
// (the slide in its place, the speaker's place as a plain shape); --report writes the
// layout (scale, fits, every text with its box) the core checks.
//
// Image files scenes ask for (screenshots, logos) are read only from inside the job
// folder: $VIDEOCUT_JOB_DIR, else the folder of overlays.json. Logos may also come
// from the brands folders ($VIDEOCUT_HOME/brands, the bundled brands/).
//
// Deterministic: frame i depends only on overlays.json and i (no clock, no randomness).
// Prints whatever it wants, then exactly one JSON summary line last; exits 0 or 1.

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const TOOL = "overlay_render";
const ROOT = path.resolve(__dirname, "..");
const TOOLS = path.join(ROOT, ".tools");

// Prefer the repository's pinned tools when run outside tool_env.
if (fs.existsSync(path.join(TOOLS, "bin"))) {
  const bin = path.join(TOOLS, "bin");
  if (!(process.env.PATH || "").split(path.delimiter).includes(bin))
    process.env.PATH = `${bin}${path.delimiter}${process.env.PATH || ""}`;
}
if (!process.env.PLAYWRIGHT_BROWSERS_PATH && fs.existsSync(path.join(TOOLS, "ms-playwright")))
  process.env.PLAYWRIGHT_BROWSERS_PATH = path.join(TOOLS, "ms-playwright");

const { chromium } = require("playwright-core");

class UsageError extends Error {}

function parseArgs(argv) {
  const a = { blur: 1 };
  const rest = argv.slice();
  const take = (flag, n = 1) => {
    if (rest.length < n) throw new UsageError(`${flag} needs ${n} value(s)`);
    return rest.splice(0, n);
  };
  while (rest.length) {
    const arg = rest.shift();
    if (arg === "--out") [a.out] = take(arg);
    else if (arg === "--boxes") [a.boxes] = take(arg);
    else if (arg === "--sheet") [a.sheet] = take(arg);
    else if (arg === "--report") [a.report] = take(arg);
    else if (arg === "--still") {
      const [t, png] = take(arg, 2);
      a.still = { t: Number(t), png };
      if (!Number.isFinite(a.still.t)) throw new UsageError("--still needs a time in seconds");
    } else if (arg === "--blur") {
      a.blur = Number(take(arg)[0]);
      if (!Number.isInteger(a.blur) || a.blur < 1 || a.blur > 32)
        throw new UsageError("--blur needs a whole number of subframes (1-32)");
    } else if (arg.startsWith("--")) throw new UsageError(`unknown option ${arg}`);
    else if (!a.input) a.input = arg;
    else throw new UsageError(`unexpected argument ${arg}`);
  }
  if (!a.input) throw new UsageError("missing <overlays.json>");
  if (!a.out && !a.still && !a.sheet && !a.report) throw new UsageError("nothing to do: pass --out, --still, --sheet or --report");
  return a;
}

// --- page bundle -----------------------------------------------------------------

// lib/ and scenes/ are plain CommonJS so node --test can load them; in the page they
// run through this tiny module loader.
function bundle() {
  const mods = [];
  for (const dir of ["lib", "scenes", "slides"]) {
    for (const f of fs.readdirSync(path.join(__dirname, dir)).sort()) {
      if (!f.endsWith(".js")) continue;
      const id = `${dir}/${f.slice(0, -3)}`;
      const src = fs.readFileSync(path.join(__dirname, dir, f), "utf8");
      mods.push(`__mods[${JSON.stringify(id)}] = function (module, exports, require) {\n${src}\n};`);
    }
  }
  return `
const __mods = {}, __cache = {};
function __require(from, spec) {
  const parts = from.split("/").slice(0, -1);
  for (const p of spec.replace(/\\.js$/, "").split("/")) {
    if (p === "..") parts.pop(); else if (p !== ".") parts.push(p);
  }
  const id = parts.join("/");
  if (!(id in __cache)) {
    if (!__mods[id]) throw new Error("module not found: " + spec + " from " + from);
    const module = { exports: {} };
    __cache[id] = module;
    __mods[id](module, module.exports, (s) => __require(id, s));
  }
  return __cache[id].exports;
}
${mods.join("\n")}
window.__load = (id) => __require("", id);
`;
}

// Runs inside the page. Everything it needs arrives as data; no clock, no randomness.
function pageMain(spec) {
  const { size, fps, safe, face, views, hidden, tokens, items, sceneNames, fontFamily } = spec;
  const images = window.__images || {};
  const { faceAt, faceDuring } = window.__load("lib/layout");
  const { step } = window.__load("lib/spring");
  const scenes = {};
  for (const n of sceneNames) scenes[n] = window.__load(`scenes/${n}`);

  const mk = (w, h) => {
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    return c;
  };
  const main = mk(size[0], size[1]);
  const acc = mk(size[0], size[1]);
  const ctx = main.getContext("2d");
  const accCtx = acc.getContext("2d");
  const memo = new Map();
  // Product demo (overlays.json "views", flattened by validViews): every item keeps off a
  // screen view that does not fill the frame and off every zoom target.
  // The slide part of a slide_split view is hard: text goes in the speaker part only.
  // A caption may sit on the seam of a portrait/square slide_split instead: there only
  // the slide's content box is hard (env.seamDuring; lib/layout.js placeBlock "seam").
  const blocks = (views || []).map((v) => ({ t0: v.t0, t1: v.t1, box: v.screen, hard: !!v.hard }));
  const seams = (views || []).filter((v) => v.seam);
  const env = {
    tokens,
    size,
    width: size[0],
    height: size[1],
    safe,
    fonts: { family: fontFamily, ready: true },
    faceAt: (t) => faceAt(face, t),
    faceDuring: (t0, t1) => faceDuring(face, t0, t1),
    spring: step,
    once: (item, fn) => {
      if (!memo.has(item)) memo.set(item, fn());
      return memo.get(item);
    },
    // Blocks already placed that are on screen at some point in [t0, t1].
    blocksDuring: (t0, t1) => blocks.filter((b) => b.t0 < t1 && b.t1 > t0).map((b) => b.box),
    hardDuring: (t0, t1) => blocks.filter((b) => b.hard && b.t0 < t1 && b.t1 > t0).map((b) => b.box),
    // The seam of a slide_split view on screen at some point in [t0, t1] (the first one):
    // {y, content (its hard boxes), parts (the slide parts it replaces as obstacles)}.
    seamDuring: (t0, t1) => {
      const on = seams.filter((v) => v.t0 < t1 && v.t1 > t0);
      if (!on.length) return null;
      return { y: on[0].seam, content: on.map((v) => v.content), parts: on.map((v) => v.screen) };
    },
    addBlock: (t0, t1, box) => blocks.push({ t0, t1, box }),
    // Decoded image for an asset reference a scene declared (null when not loaded),
    // and an offscreen canvas for scenes that prepare images once (redaction).
    image: (ref) => images[ref] || null,
    canvas: mk,
  };

  // Lay out every item once, in time order, so placement never depends on which
  // frames get rendered (--still, --sheet and the full render agree). Items a scene marks
  // `layoutFirst` (a portrait screenshot that fills the stage) go before the rest.
  const first = items.map((it) => {
    const s = scenes[it.scene];
    return s && s.layoutFirst && s.layoutFirst(it, env) ? 0 : 1;
  });
  const order = items
    .map((it, i) => i)
    .sort((a, b) => first[a] - first[b] || items[a].t0 - items[b].t0 || a - b);
  const placement = { face: 0, block: 0, cramped: 0 };
  for (const i of order) {
    const scene = scenes[items[i].scene];
    if (!scene.layout) continue;
    ctx.save();
    const L = scene.layout(ctx, items[i], env);
    ctx.restore();
    if (L && L.overlapsFace) placement.face++;
    if (L && L.overlapsBlock) placement.block++;
    if (L && !L.fits) placement.cramped++;
  }

  // Full-frame plate items (scene.plate, the end card): no caption is drawn while one is
  // on screen.
  const { platesOf, hidesCaptions } = window.__load("lib/plates");
  const plates = platesOf(items, scenes);

  // Draw every item alive at output time t onto ctx; returns [[index, box, t], ...].
  function drawAt(t) {
    const boxes = [];
    const hideCaptions = hidesCaptions(plates, t);
    // A full-frame slide (overlays.json views with "hide"): no caption, no overlay item.
    const slide = (hidden || []).some((h) => t >= h.t0 && t < h.t1);
    items.forEach((item, i) => {
      if (t < item.t0 || t > item.t1) return;
      if (hideCaptions && item.scene === "caption") return;
      if (slide && !scenes[item.scene].plate) return;
      ctx.save();
      const box = scenes[item.scene].draw(ctx, t - item.t0, item, env);
      ctx.restore();
      if (box) boxes.push([i, box, t]);
    });
    return boxes;
  }

  // Frame at time t with `sub` subframes spread over one frame interval (motion blur).
  function render(t, sub) {
    if (sub <= 1) {
      ctx.clearRect(0, 0, size[0], size[1]);
      return { canvas: main, boxes: drawAt(t) };
    }
    accCtx.clearRect(0, 0, size[0], size[1]);
    accCtx.globalCompositeOperation = "lighter";
    accCtx.globalAlpha = 1 / sub;
    const boxes = [];
    for (let k = 0; k < sub; k++) {
      ctx.clearRect(0, 0, size[0], size[1]);
      boxes.push(...drawAt(t + ((k + 0.5) / sub - 0.5) / fps));
      accCtx.drawImage(main, 0, 0);
    }
    return { canvas: acc, boxes };
  }

  // Boxes only (no image) for a list of frame times: --boxes without --out.
  window.boxesFor = (times, sub) => times.flatMap((t) => render(t, sub).boxes);

  window.frame = (t, sub) => {
    const r = render(t, sub);
    return { png: r.canvas.toDataURL("image/png"), boxes: r.boxes };
  };

  // `opt.labels` (one per time) replaces the time label, `opt.background` (a colour)
  // replaces the checkerboard: the brand preview sheet shows scenes on the brand's own
  // background.
  window.sheet = (times, sub, opt = {}) => {
    const cols = 3;
    const rows = Math.ceil(times.length / cols);
    const cw = Math.round(size[0] / cols);
    const ch = Math.round((cw * size[1]) / size[0]);
    const s = mk(cw * cols, ch * rows);
    const sc = s.getContext("2d");
    // Checkerboard so transparent and opaque areas are both visible (tooling, not brand).
    const cell = 16;
    if (opt.background) {
      sc.fillStyle = opt.background;
      sc.fillRect(0, 0, s.width, s.height);
    } else
      for (let y = 0; y < s.height; y += cell)
        for (let x = 0; x < s.width; x += cell) {
          sc.fillStyle = ((x + y) / cell) % 2 ? "#9a9a9a" : "#6e6e6e";
          sc.fillRect(x, y, cell, cell);
        }
    const boxes = [];
    times.forEach((t, k) => {
      const r = render(t, sub);
      boxes.push(...r.boxes);
      const x = (k % cols) * cw;
      const y = Math.floor(k / cols) * ch;
      sc.drawImage(r.canvas, x, y, cw, ch);
      sc.strokeStyle = "#000";
      sc.strokeRect(x + 0.5, y + 0.5, cw - 1, ch - 1);
      const label = opt.labels && opt.labels[k] != null ? String(opt.labels[k]) : `${t.toFixed(2)} s`;
      sc.font = "20px monospace";
      sc.fillStyle = "#000";
      sc.fillRect(x + 1, y + 1, Math.ceil(sc.measureText(label).width) + 16, 30);
      sc.fillStyle = "#fff";
      sc.textBaseline = "top";
      sc.fillText(label, x + 8, y + 6);
    });
    return { jpg: s.toDataURL("image/jpeg", 0.88), boxes };
  };

  window.placement = placement;

  window.blank = () => {
    ctx.clearRect(0, 0, size[0], size[1]);
    return main.toDataURL("image/png");
  };
}

// Runs inside the page for a slide spec ("mode": "slide"). Everything arrives as data.
function pageSlide(spec) {
  const { size, fps, duration, tokens, slide, unit, lang, frame, fontFamily } = spec;
  const images = window.__images || {};
  const S = window.__load("lib/slide");
  const mod = window.__load(`slides/${slide.template}`);
  const mk = (w, h) => {
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    return c;
  };
  const main = mk(size[0], size[1]);
  const ctx = main.getContext("2d");
  const env = {
    tokens,
    size,
    width: size[0],
    height: size[1],
    unit,
    lang,
    fps,
    duration,
    fonts: { family: fontFamily, ready: true },
    image: (ref) => images[ref] || null,
    canvas: mk,
  };
  env.times = S.stepTimes(mod.steps(slide.content), slide.reveal);
  // Candidate content boxes (slide: above or beside the bubble): the one where the text
  // fits at the largest scale (an image: the largest picture, `rank`) wins; the first on
  // a tie.
  let L = null;
  for (const area of Array.isArray(slide.areas) && slide.areas.length ? slide.areas : [slide.area]) {
    ctx.save();
    const c = mod.layout(ctx, { ...slide, area }, env);
    ctx.restore();
    const rank = (x) => (Number.isFinite(x.rank) ? x.rank : x.k);
    if (!L || (c.fits && !L.fits) || (c.fits === L.fits && rank(c) > rank(L))) L = c;
  }

  function draw(t) {
    ctx.save();
    S.plate(ctx, env);
    mod.draw(ctx, t, slide, env, L);
    ctx.restore();
    return main;
  }

  window.frame = (t) => ({ png: draw(t).toDataURL("image/png") });

  // From this clip time on every frame is the same (all builds have arrived): the node
  // side reuses the last picture. The slowest movement is a bar growing at 0.6 of the
  // entrance pace after a 0.1 s delay; a template with longer motion says so (staticFrom).
  const last = Math.max(0.8, ...env.times);
  window.staticFrom = mod.staticFrom ? mod.staticFrom(env, L) : last + 0.1 + S.enterTime(tokens) / 0.6 + 2 / fps;

  // The whole output frame: the plate, the slide in its rect, the speaker (its picture
  // when the spec has one, else a plain shape in the surface or muted colour).
  window.composite = (t) => {
    const [W, H] = frame.size;
    const out = mk(W, H);
    const c = out.getContext("2d");
    const pal = S.palette(tokens);
    c.fillStyle = pal.plate;
    c.fillRect(0, 0, W, H);
    c.drawImage(draw(t), frame.rect[0], frame.rect[1], frame.rect[2], frame.rect[3]);
    const sp = frame.speaker;
    if (sp) {
      const [x0, y0, x1, y1] = sp.box;
      c.save();
      c.beginPath();
      if (sp.round) c.arc((x0 + x1) / 2, (y0 + y1) / 2, Math.min(x1 - x0, y1 - y0) / 2, 0, Math.PI * 2);
      else c.rect(x0, y0, x1 - x0, y1 - y0);
      const img = sp.image ? images[sp.image] : null;
      if (img) {
        c.clip();
        c.drawImage(img, x0, y0, x1 - x0, y1 - y0);
      } else {
        c.fillStyle = pal.surface || pal.muted;
        c.fill();
      }
      c.restore();
    }
    return { png: out.toDataURL("image/png") };
  };

  window.report = {
    template: slide.template,
    area: L.area,
    scale: L.k,
    fits: Boolean(L.fits),
    missing: Boolean(L.missing),
    texts: L.texts || [],
    words: S.words(L.texts || []),
    times: env.times.map((v) => Math.round(v * 1000) / 1000),
    entrance_s: Math.round(S.enterTime(tokens) * 1000) / 1000,
  };

  window.blank = () => draw(0).toDataURL("image/png");
}

// --- node side -------------------------------------------------------------------

const FONT_FAMILY = "videocut-brand";

// Brand font files as {weight, base64}. Values are absolute paths (resolved tokens),
// file:// URLs or data URIs; relative paths resolve against the overlays.json folder.
function readFonts(tokens, baseDir) {
  const files = (tokens.fonts && tokens.fonts.files) || {};
  return Object.entries(files)
    .filter(([, ref]) => typeof ref === "string" && ref)
    .map(([weight, ref]) => {
      if (ref.startsWith("data:")) {
        const m = /^data:[^,]*;base64,(.*)$/s.exec(ref);
        if (!m) throw new Error(`font ${weight}: only base64 data URIs are supported`);
        return { weight, b64: m[1] };
      }
      const file = ref.startsWith("file://") ? decodeURIComponent(new URL(ref).pathname) : path.resolve(baseDir, ref);
      return { weight, b64: fs.readFileSync(file).toString("base64") };
    });
}

// Decode the scenes' image assets in the page before the first layout.
async function loadImages(page, assets) {
  return page.evaluate(async (assets) => {
    window.__images = {};
    for (const a of assets) {
      const img = new Image();
      img.src = `data:${a.mime};base64,${a.b64}`;
      await img.decode();
      window.__images[a.ref] = img;
    }
    return Object.keys(window.__images).length;
  }, assets);
}

async function loadFonts(page, fonts) {
  return page.evaluate(
    async ({ fonts, family }) => {
      const out = [];
      for (const f of fonts) {
        const bytes = Uint8Array.from(atob(f.b64), (c) => c.charCodeAt(0));
        const face = new FontFace(family, bytes.buffer, { weight: String(f.weight) });
        await face.load();
        document.fonts.add(face);
        out.push(Number(f.weight));
      }
      await document.fonts.ready;
      return out.filter((w) => document.fonts.check(`${w} 48px "${family}"`)).length;
    },
    { fonts, family: FONT_FAMILY },
  );
}

function items(spec, known) {
  const list = [];
  const skipped = [];
  for (const o of spec.overlays || []) {
    // Caption groups come only from spec.captions (they need words).
    if (known.includes(o.scene) && o.scene !== "caption") list.push({ scene: o.scene, t0: o.t0, t1: o.t1, props: o.props || {} });
    else skipped.push(o.scene);
  }
  // Captions last, so they draw on top of cards.
  for (const c of require("./lib/seams").splitAtSeams(spec.captions || [], validViews(spec.views)))
    list.push({ scene: "caption", t0: c.t0, t1: c.t1, words: c.words || [] });
  return { list, skipped };
}

// Areas to keep clear in a product demo, flattened to {t0, t1, screen: [x0, y0, x1, y1]}
// (output time/px): each view's `screen` (null when the screen fills the frame: it is the
// picture; `hard` for the slide part of a slide_split view, with its `seam` y and
// `content` box when the parts are stacked) and each of its `focus` zoom
// targets ({t0, t1, box}). Malformed entries are dropped.
function validViews(views) {
  const box = (b) => Array.isArray(b) && b.length === 4 && b.every(Number.isFinite) && b[2] > b[0] && b[3] > b[1];
  const span = (v) => v && Number.isFinite(v.t0) && Number.isFinite(v.t1) && v.t0 < v.t1;
  const out = [];
  for (const v of Array.isArray(views) ? views : []) {
    if (!span(v)) continue;
    if (box(v.screen)) {
      const split = v.layout === "slide_split";
      const seam = split && Number.isFinite(v.seam) && box(v.content) ? { seam: v.seam, content: v.content.slice() } : {};
      out.push({ t0: v.t0, t1: v.t1, screen: v.screen.slice(), hard: split, ...seam });
    }
    for (const f of Array.isArray(v.focus) ? v.focus : []) {
      if (span(f) && box(f.box)) out.push({ t0: f.t0, t1: f.t1, screen: f.box.slice() });
    }
  }
  return out;
}

// Output spans [t0, t1) of the views marked "hide" (a full-frame slide).
function hiddenSpans(views) {
  return (Array.isArray(views) ? views : [])
    .filter((v) => v && v.hide === true && Number.isFinite(v.t0) && Number.isFinite(v.t1) && v.t0 < v.t1)
    .map((v) => ({ t0: v.t0, t1: v.t1 }));
}

// The frame size: spec.size, else spec.width x spec.height, else 1080x1920 (the 9:16
// reel every call used before output formats existed). Written back as spec.size so the
// rest of the renderer has one place to read it from.
function resolveSize(spec) {
  if (spec.size === undefined) spec.size = [spec.width ?? 1080, spec.height ?? 1920];
  return spec;
}

function validate(spec) {
  const errs = [];
  const okSize = Array.isArray(spec.size) && spec.size.length === 2 && spec.size.every((v) => Number.isInteger(v) && v > 0);
  if (!okSize) errs.push("size must be [width, height] in px");
  if (!(spec.fps > 0)) errs.push("fps must be > 0");
  if (!(spec.duration > 0)) errs.push("duration must be > 0 seconds");
  if (!spec.tokens || typeof spec.tokens !== "object") errs.push("tokens missing");
  for (const it of [...(spec.captions || []), ...(spec.overlays || [])])
    if (!(Number.isFinite(it.t0) && Number.isFinite(it.t1) && it.t0 < it.t1)) {
      errs.push("every caption/overlay needs numbers t0 < t1");
      break;
    }
  if (errs.length) throw new Error(`overlays.json: ${errs.join("; ")}`);
}

// ffmpeg rate argument: NTSC-style rates (29.97, 59.94, 23.976) as exact rationals.
function rate(fps) {
  const n = Math.round((fps * 1001) / 1000);
  if (!Number.isInteger(fps) && Math.abs((n * 1000) / 1001 - fps) < 0.005) return `${n * 1000}/1001`;
  return String(fps);
}

function startFfmpeg(out, size, fps, opaque = false) {
  // An opaque slide clip: ProRes 422 HQ without alpha; an overlay: ProRes 4444 with alpha.
  const codec = opaque ? ["-profile:v", "3", "-pix_fmt", "yuv422p10le"] : ["-profile:v", "4444", "-pix_fmt", "yuva444p10le"];
  const ff = spawn(
    "ffmpeg",
    [
      "-y", "-loglevel", "error",
      "-f", "image2pipe", "-framerate", rate(fps), "-c:v", "png", "-i", "-",
      // BT.709 limited range, tagged: render composites it into a BT.709 reel (an
      // untagged RGB -> YUV conversion would use BT.601 and shift the brand colours).
      "-vf", `scale=${size[0]}:${size[1]}:out_color_matrix=bt709:out_range=tv`,
      "-c:v", "prores_ks", ...codec,
      "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
      "-color_range", "tv", out,
    ],
    { stdio: ["pipe", "ignore", "pipe"] },
  );
  let err = "";
  let failed = null;
  ff.stderr.on("data", (d) => (err += d));
  // A broken pipe shows up as ffmpeg's exit below; the error itself carries nothing new.
  ff.stdin.on("error", () => {});
  const done = new Promise((res, rej) => {
    ff.on("error", (e) => rej((failed = e)));
    ff.on("close", (code) => {
      if (code === 0) return res();
      rej((failed = new Error(`ffmpeg exited ${code}: ${err.trim().slice(-400)}`)));
    });
  });
  done.catch(() => {}); // surfaced by write() or end()
  const write = async (buf) => {
    if (failed) throw failed;
    if (!ff.stdin.write(buf)) await Promise.race([new Promise((r) => ff.stdin.once("drain", r)), done]);
    if (failed) throw failed;
  };
  return { write, end: () => (ff.stdin.end(), done), kill: () => ff.kill("SIGKILL") };
}

// Nine moments across the duration, one per ninth: its centre, or, when nothing is fully
// in at the centre (a gap or a transition), the middle of the item nearest to it that
// lives in that ninth. The same input always gives the same times.
function sheetTimes(list, duration, n = 9) {
  const d = duration / n;
  const settled = (it, t) => t >= it.t0 + 0.2 * (it.t1 - it.t0) && t <= it.t1 - 0.2 * (it.t1 - it.t0);
  return Array.from({ length: n }, (_, k) => {
    const c = d * (k + 0.5);
    if (list.some((it) => settled(it, c))) return c;
    const mids = list.map((it) => (it.t0 + it.t1) / 2).filter((m) => m >= k * d && m < (k + 1) * d);
    return mids.length ? mids.reduce((a, b) => (Math.abs(b - c) < Math.abs(a - c) ? b : a)) : c;
  });
}

const decode = (dataUrl) => Buffer.from(dataUrl.slice(dataUrl.indexOf(",") + 1), "base64");
const round = (v, n = 2) => Math.round(v * 10 ** n) / 10 ** n;

const SLIDES_DIR = path.join(__dirname, "slides");
const slideTemplates = () => fs.readdirSync(SLIDES_DIR).filter((f) => f.endsWith(".js")).map((f) => f.slice(0, -3)).sort();

function validateSlide(spec) {
  const errs = [];
  const okSize = (v) => Array.isArray(v) && v.length === 2 && v.every((x) => Number.isInteger(x) && x > 0);
  const okBox = (b) => Array.isArray(b) && b.length === 4 && b.every(Number.isFinite) && b[2] > b[0] && b[3] > b[1];
  if (!okSize(spec.size)) errs.push("size must be [width, height] in px");
  if (!(spec.fps > 0)) errs.push("fps must be > 0");
  if (!(spec.duration > 0)) errs.push("duration must be > 0 seconds");
  if (!spec.tokens || typeof spec.tokens !== "object") errs.push("tokens missing");
  if (!(spec.unit > 0)) errs.push("unit must be > 0 (the output frame's short side)");
  const sl = spec.slide;
  if (!sl || typeof sl !== "object") errs.push("slide missing");
  else {
    if (!slideTemplates().includes(sl.template)) errs.push(`slide.template must be one of ${slideTemplates().join(", ")}`);
    if (!sl.content || typeof sl.content !== "object") errs.push("slide.content missing");
    if (!okBox(sl.area)) errs.push("slide.area must be [x0, y0, x1, y1] in clip px");
    if (sl.reveal != null && !(Array.isArray(sl.reveal) && sl.reveal.every(Number.isFinite))) errs.push("slide.reveal must be a list of seconds");
    if (sl.areas != null && !(Array.isArray(sl.areas) && sl.areas.every(okBox))) errs.push("slide.areas must be a list of [x0, y0, x1, y1]");
  }
  const fr = spec.frame;
  if (fr != null) {
    const okRect = Array.isArray(fr.rect) && fr.rect.length === 4 && fr.rect.every(Number.isFinite);
    if (!okSize(fr.size) || !okRect) errs.push("frame needs size [w, h] and rect [x, y, w, h]");
    if (fr.speaker != null && !okBox(fr.speaker.box)) errs.push("frame.speaker.box must be [x0, y0, x1, y1]");
  }
  if (errs.length) throw new Error(`slide spec: ${errs.join("; ")}`);
}

// One slide as an opaque clip / a still / its layout report.
async function slideMain(args, spec) {
  validateSlide(spec);
  if (args.sheet || args.boxes) throw new UsageError("--sheet and --boxes are for overlays, not slides");
  const { size, fps } = spec;
  const fonts = readFonts(spec.tokens, path.dirname(path.resolve(args.input)));
  const mod = require(`./slides/${spec.slide.template}`);
  const home = process.env.VIDEOCUT_HOME || path.join(require("os").homedir(), "VideoCut");
  const roots = {
    job: path.resolve(process.env.VIDEOCUT_JOB_DIR || path.dirname(path.resolve(args.input))),
    brands: [path.join(home, "brands"), path.join(ROOT, "brands")],
  };
  const { readAsset } = require("./lib/assets");
  const wanted = typeof mod.assets === "function" ? mod.assets(spec.slide) : [];
  // The speaker's picture of a still (a camera frame the core cut out), from the job.
  const sp = spec.frame && spec.frame.speaker;
  if (sp && typeof sp.image === "string") wanted.push({ ref: sp.image, root: "job" });
  const assets = wanted.map((a) => readAsset(a, roots));
  const summary = { tool: TOOL, ok: true, mode: "slide", template: spec.slide.template, size, fps, duration: spec.duration };
  const t0 = process.hrtime.bigint();
  const browser = await chromium.launch();
  let ff = null;
  try {
    const page = await browser.newPage({ viewport: { width: 64, height: 64 } });
    await page.setContent("<!doctype html><html><body></body></html>");
    await page.addScriptTag({ content: bundle() });
    const loaded = fonts.length ? await loadFonts(page, fonts) : 0;
    if (loaded < fonts.length) throw new Error(`brand fonts: ${loaded} of ${fonts.length} files loaded`);
    summary.fonts = { files: fonts.length, loaded, fallback: fonts.length === 0 };
    if (assets.length) summary.images = await loadImages(page, assets);
    const fontFamily = fonts.length ? `"${FONT_FAMILY}", system-ui, sans-serif` : "system-ui, sans-serif";
    await page.evaluate(pageSlide, {
      size, fps, duration: spec.duration, tokens: spec.tokens, slide: spec.slide, unit: spec.unit,
      lang: spec.lang === "en" ? "en" : "de", frame: spec.frame || null, fontFamily,
    });
    const report = await page.evaluate(() => window.report);
    summary.fits = report.fits;
    summary.scale = report.scale;
    if (args.report) {
      fs.writeFileSync(args.report, JSON.stringify(report, null, 2) + "\n");
      summary.report = args.report;
    }
    if (args.still) {
      const fn = spec.frame ? "composite" : "frame";
      const r = await page.evaluate(([t, f]) => window[f](t), [args.still.t, fn]);
      fs.writeFileSync(args.still.png, decode(r.png));
      summary.still = args.still.png;
    }
    if (args.out) {
      const frames = Math.max(1, Math.round(spec.duration * fps));
      ff = startFfmpeg(args.out, size, fps, true);
      const r0 = process.hrtime.bigint();
      const still = await page.evaluate(() => window.staticFrom);
      let png = null;
      let drawn = 0;
      for (let i = 0; i < frames; i++) {
        const t = i / fps;
        if (!png || t < still) {
          png = decode((await page.evaluate((t) => window.frame(t), t)).png);
          drawn++;
        }
        await ff.write(png);
      }
      summary.drawn_frames = drawn;
      await ff.end();
      ff = null;
      const secs = Number(process.hrtime.bigint() - r0) / 1e9;
      Object.assign(summary, { out: args.out, frames, render_seconds: round(secs), frames_per_second: round(frames / secs, 1) });
    }
  } finally {
    if (ff) ff.kill();
    await browser.close();
  }
  summary.seconds = round(Number(process.hrtime.bigint() - t0) / 1e9);
  return summary;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const raw = JSON.parse(fs.readFileSync(args.input, "utf8"));
  if (raw && raw.mode === "slide") return slideMain(args, raw);
  if (args.report) throw new UsageError("--report is for slides");
  const spec = resolveSize(raw);
  validate(spec);
  const size = spec.size;
  const fps = spec.fps;
  const sceneNames = fs.readdirSync(path.join(__dirname, "scenes")).filter((f) => f.endsWith(".js")).map((f) => f.slice(0, -3)).sort();
  const { list, skipped } = items(spec, sceneNames);
  const fonts = readFonts(spec.tokens, path.dirname(path.resolve(args.input)));
  const sceneMods = Object.fromEntries(sceneNames.map((n) => [n, require(`./scenes/${n}`)]));
  const home = process.env.VIDEOCUT_HOME || path.join(require("os").homedir(), "VideoCut");
  const assets = require("./lib/assets").collectAssets(list, sceneMods, spec.tokens, {
    job: path.resolve(process.env.VIDEOCUT_JOB_DIR || path.dirname(path.resolve(args.input))),
    brands: [path.join(home, "brands"), path.join(ROOT, "brands"), ...(process.env.VIDEOCUT_BRAND_DIR ? [process.env.VIDEOCUT_BRAND_DIR] : [])],
  });
  const face = (spec.face || []).filter((f) => Number.isFinite(f.t) && Array.isArray(f.box)).sort((a, b) => a.t - b.t);
  const summary = { tool: TOOL, ok: true, size, fps, duration: spec.duration, items: list.length };
  if (skipped.length) summary.skipped_scenes = [...new Set(skipped)];

  const t0 = process.hrtime.bigint();
  const browser = await chromium.launch();
  let ff = null;
  try {
    const page = await browser.newPage({ viewport: { width: 64, height: 64 } });
    await page.setContent("<!doctype html><html><body></body></html>");
    await page.addScriptTag({ content: bundle() });
    const loaded = fonts.length ? await loadFonts(page, fonts) : 0;
    if (loaded < fonts.length) throw new Error(`brand fonts: ${loaded} of ${fonts.length} files loaded`);
    summary.fonts = { files: fonts.length, loaded, fallback: fonts.length === 0 };
    if (assets.length) summary.images = await loadImages(page, assets);
    const fontFamily = fonts.length ? `"${FONT_FAMILY}", system-ui, sans-serif` : "system-ui, sans-serif";
    await page.evaluate(pageMain, {
      size, fps, safe: spec.safe || {}, face, views: validViews(spec.views), hidden: hiddenSpans(spec.views), tokens: spec.tokens, items: list, sceneNames, fontFamily,
    });
    // Items that could not be placed clear of the face / of each other / inside the safe
    // area even at their smallest size (no room left).
    const p = await page.evaluate(() => window.placement);
    summary.overlaps = { face: p.face, blocks: p.block, safe_zone: p.cramped };

    const collected = new (require("./lib/boxes").BoxCollector)(size);
    const collect = (pairs) => {
      for (const [i, box, t] of pairs) {
        const it = list[i];
        collected.add(i, { scene: it.scene, t0: it.t0, t1: it.t1 }, box, t);
      }
    };

    if (args.still) {
      const r = await page.evaluate(([t, sub]) => window.frame(t, sub), [args.still.t, args.blur]);
      fs.writeFileSync(args.still.png, decode(r.png));
      summary.still = args.still.png;
    }
    if (args.sheet) {
      // overlays.json may fix the sheet: {"sheet": {"times": [s], "labels": [..], "background": "#hex"}}
      const fixed = spec.sheet && typeof spec.sheet === "object" ? spec.sheet : {};
      const times = Array.isArray(fixed.times) && fixed.times.length ? fixed.times : sheetTimes(list, spec.duration);
      const opt = { labels: fixed.labels, background: fixed.background };
      const r = await page.evaluate(([ts, sub, o]) => window.sheet(ts, sub, o), [times, args.blur, opt]);
      fs.writeFileSync(args.sheet, decode(r.jpg));
      summary.sheet = args.sheet;
    }
    // Boxes always cover every frame of the output, also when only --still/--sheet run.
    const frames = Math.max(1, Math.round(spec.duration * fps));
    // Frames where no item can draw (incl. the motion-blur shutter) reuse one blank PNG.
    const half = args.blur > 1 ? 0.5 / fps : 0;
    const active = (t) => list.some((it) => t + half >= it.t0 && t - half <= it.t1);
    if (args.boxes && !args.out) {
      const times = Array.from({ length: frames }, (_, i) => i / fps).filter(active);
      for (let i = 0; i < times.length; i += 60)
        collect(await page.evaluate(([ts, sub]) => window.boxesFor(ts, sub), [times.slice(i, i + 60), args.blur]));
    }
    if (args.out) {
      const blank = decode(await page.evaluate(() => window.blank()));
      ff = startFfmpeg(args.out, size, fps);
      let drawn = 0;
      const r0 = process.hrtime.bigint();
      for (let i = 0; i < frames; i++) {
        const t = i / fps;
        if (!active(t)) {
          await ff.write(blank);
          continue;
        }
        const r = await page.evaluate(([t, sub]) => window.frame(t, sub), [t, args.blur]);
        collect(r.boxes);
        drawn++;
        await ff.write(decode(r.png));
      }
      await ff.end();
      ff = null;
      const secs = Number(process.hrtime.bigint() - r0) / 1e9;
      Object.assign(summary, {
        out: args.out,
        frames,
        drawn_frames: drawn,
        blank_frames: frames - drawn,
        blur_subframes: args.blur,
        render_seconds: round(secs),
        frames_per_second: round(frames / secs, 1),
      });
    }
    if (args.boxes) {
      const listed = collected.list();
      fs.writeFileSync(args.boxes, JSON.stringify(listed, null, 2) + "\n");
      // Items that never drew anything (empty text, outside the duration) have no box.
      summary.undrawn_items = list.length - listed.length;
      summary.boxes = args.boxes;
    }
  } finally {
    if (ff) ff.kill();
    await browser.close();
  }
  summary.seconds = round(Number(process.hrtime.bigint() - t0) / 1e9);
  return summary;
}

main()
  .then((s) => {
    console.log(JSON.stringify(s));
    process.exit(0);
  })
  .catch((e) => {
    if (!(e instanceof UsageError)) console.error(e && e.stack ? e.stack : e);
    console.log(JSON.stringify({ tool: TOOL, ok: false, error: String(e && e.message ? e.message : e) }));
    process.exit(1);
  });
