#!/usr/bin/env node
// Motion scene renderer (spike): a scene module written per beat (HTML/SVG/CSS under
// anime.js, renderer/motion/) rendered frame by frame in a sandboxed headless Chromium
// page, as ProRes 4444 with alpha.
//
//   node motion.js <motion.json> [--out <scene.mov>] [--stills <dir> <t,t,...>]
//                  [--frame-timeout <ms>] [--region-only]
//
// motion.json: {"mode": "motion", "size": [w, h], "fps", "duration" (s), "unit" (short
// side), "region": [x0, y0, x1, y1] (where the stage sits), "tokens" (resolved brand
// tokens), "words": [{w, t0, t1}] (scene seconds), "beats": {id: {word, n?, offset?, edge?}},
// "scene": path of the module inside the job folder, "seed"}.
//
// Sandbox: the scene is untrusted (agent-written). The page lives on a private origin;
// every request is answered by this process (the runtime, the brand fonts, files inside
// the job folder) or aborted, and any blocked request or CSP violation fails the render.
// CSP allows scripts only from the page's own origin and no connections at all; network
// APIs throw; the page has no Node APIs; every frame has a time limit.
//
// Deterministic: frame i depends only on motion.json, the scene module and i.
// Prints exactly one JSON summary line last; exits 0 or 1.

const fs = require("fs");
const path = require("path");

const TOOL = "motion_render";
const ROOT = path.resolve(__dirname, "..");
const TOOLS = path.join(ROOT, ".tools");
if (fs.existsSync(path.join(TOOLS, "bin"))) {
  const bin = path.join(TOOLS, "bin");
  if (!(process.env.PATH || "").split(path.delimiter).includes(bin)) process.env.PATH = `${bin}${path.delimiter}${process.env.PATH || ""}`;
}
if (!process.env.PLAYWRIGHT_BROWSERS_PATH && fs.existsSync(path.join(TOOLS, "ms-playwright")))
  process.env.PLAYWRIGHT_BROWSERS_PATH = path.join(TOOLS, "ms-playwright");

const { chromium } = require("playwright-core");
const { startFfmpeg } = require("./lib/ffmpeg");
const { literalErrors } = require("./lib/literals");

const ORIGIN = "https://scene.invalid";
const FONT_FAMILY = "vc-brand";
const CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self'",
  "img-src 'self' data: blob:",
  "media-src 'self'",
  "connect-src 'none'",
  "worker-src 'none'",
  "frame-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join("; ");

class UsageError extends Error {}

function parseArgs(argv) {
  const a = { frameTimeout: 2000 };
  const rest = argv.slice();
  while (rest.length) {
    const arg = rest.shift();
    if (arg === "--out") a.out = rest.shift();
    else if (arg === "--stills") {
      a.stillsDir = rest.shift();
      a.stillTimes = String(rest.shift() || "").split(",").filter(Boolean).map(Number);
      if (!a.stillTimes.length || !a.stillTimes.every(Number.isFinite)) throw new UsageError("--stills needs <dir> <t,t,...>");
    } else if (arg === "--frame-timeout") a.frameTimeout = Number(rest.shift());
    else if (arg === "--region-only") a.regionOnly = true;
    else if (arg.startsWith("--")) throw new UsageError(`unknown option ${arg}`);
    else if (!a.input) a.input = arg;
    else throw new UsageError(`unexpected argument ${arg}`);
  }
  if (!a.input) throw new UsageError("missing <motion.json>");
  if (!a.out && !a.stillsDir) throw new UsageError("nothing to do: pass --out or --stills");
  return a;
}

// --- spec ------------------------------------------------------------------------

const norm = (s) => String(s).toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

// Beat sheet -> scene seconds. {id: {word: "phrase", n: 1, edge: "start"|"end", offset: s}}
function resolveBeats(beats, words) {
  const out = {};
  const ws = words.map((w) => norm(w.w));
  for (const [id, b] of Object.entries(beats || {})) {
    const parts = String(b.word || "").split(/\s+/).map(norm).filter(Boolean);
    const want = Number.isInteger(b.n) && b.n > 0 ? b.n : 1;
    let seen = 0;
    let hit = null;
    for (let i = 0; i + parts.length <= ws.length && parts.length; i++)
      if (parts.every((p, k) => ws[i + k] === p) && ++seen === want) {
        hit = b.edge === "end" ? words[i + parts.length - 1].t1 : words[i].t0;
        break;
      }
    if (hit == null) throw new Error(`beat "${id}": word "${b.word}" (#${want}) is not spoken in the scene`);
    out[id] = Math.round((hit + (Number.isFinite(b.offset) ? b.offset : 0)) * 1000) / 1000;
  }
  return out;
}

function validate(spec) {
  const errs = [];
  const okBox = (b) => Array.isArray(b) && b.length === 4 && b.every(Number.isFinite) && b[2] > b[0] && b[3] > b[1];
  if (!(Array.isArray(spec.size) && spec.size.length === 2 && spec.size.every((v) => Number.isInteger(v) && v > 0))) errs.push("size must be [w, h]");
  if (!(spec.fps > 0)) errs.push("fps must be > 0");
  if (!(spec.duration > 0 && spec.duration <= 120)) errs.push("duration must be 0 < s <= 120");
  if (!okBox(spec.region)) errs.push("region must be [x0, y0, x1, y1]");
  if (!spec.tokens || typeof spec.tokens !== "object") errs.push("tokens missing");
  if (typeof spec.scene !== "string") errs.push("scene (module path inside the job folder) missing");
  if (!Array.isArray(spec.words) || !spec.words.every((w) => typeof w.w === "string" && Number.isFinite(w.t0) && Number.isFinite(w.t1)))
    errs.push("words must be [{w, t0, t1}]");
  if (errs.length) throw new Error(`motion.json: ${errs.join("; ")}`);
}

// A path inside `root` (after resolving symlinks), else null.
function inside(root, rel) {
  try {
    const real = fs.realpathSync(path.resolve(root, rel));
    const r = fs.realpathSync(root);
    return real === r || real.startsWith(r + path.sep) ? real : null;
  } catch {
    return null;
  }
}

const MIME = { ".js": "text/javascript", ".mjs": "text/javascript", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml", ".webp": "image/webp", ".json": "application/json", ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf" };

function fontFiles(tokens) {
  const files = (tokens.fonts && tokens.fonts.files) || {};
  return Object.entries(files)
    .filter(([w, f]) => /^\d+$/.test(w) && typeof f === "string" && fs.existsSync(f))
    .map(([w, f]) => ({ weight: Number(w), file: f }));
}

function pageHtml(fonts) {
  const faces = fonts
    .map((f) => `@font-face{font-family:"${FONT_FAMILY}";src:url(/rt/font/${f.weight});font-weight:${f.weight};font-display:block}`)
    .join("\n");
  return `<!doctype html><html><head><meta charset="utf-8"><style>${faces}</style></head><body>
<script src="/rt/spec.js"></script><script src="/rt/anime.js"></script>
<script src="/rt/components.js"></script><script src="/rt/runtime.js"></script>
</body></html>`;
}

const withTimeout = (p, msec, what) => {
  let timer;
  return Promise.race([p, new Promise((_, rej) => (timer = setTimeout(() => rej(new Error(`sandbox: ${what} took longer than ${msec} ms`)), msec)))]).finally(() =>
    clearTimeout(timer),
  );
};

// --- render ----------------------------------------------------------------------

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const spec = JSON.parse(fs.readFileSync(args.input, "utf8"));
  validate(spec);
  const jobDir = path.resolve(process.env.VIDEOCUT_JOB_DIR || path.dirname(path.resolve(args.input)));
  const sceneFile = inside(jobDir, spec.scene);
  if (!sceneFile) throw new Error(`scene ${spec.scene} is not a file inside the job folder`);
  const sceneSrc = fs.readFileSync(sceneFile, "utf8");
  const lit = literalErrors(sceneSrc);
  if (lit.length) throw new Error(`scene ${spec.scene}: brand tokens only (${lit.join("; ")})`);

  const beats = resolveBeats(spec.beats, spec.words);
  const fonts = fontFiles(spec.tokens);
  const fontFamily = fonts.length ? `"${FONT_FAMILY}", system-ui, sans-serif` : "system-ui, sans-serif";
  const pageSpec = {
    size: spec.size,
    fps: spec.fps,
    duration: spec.duration,
    unit: spec.unit || Math.min(...spec.size),
    region: spec.region,
    tokens: spec.tokens,
    words: spec.words,
    beats,
    seed: Number.isInteger(spec.seed) ? spec.seed : 1,
    fontFamily,
  };
  const rt = (f) => fs.readFileSync(path.join(__dirname, "motion", f), "utf8");
  const files = {
    "/rt/spec.js": { body: `window.__vcSpec = ${JSON.stringify(pageSpec)};`, type: MIME[".js"] },
    "/rt/anime.js": { body: fs.readFileSync(path.join(__dirname, "node_modules", "animejs", "dist", "bundles", "anime.umd.min.js"), "utf8"), type: MIME[".js"] },
    "/rt/components.js": { body: rt("components.js"), type: MIME[".js"] },
    "/rt/runtime.js": { body: rt("runtime.js"), type: MIME[".js"] },
  };
  for (const f of fonts) files[`/rt/font/${f.weight}`] = { body: fs.readFileSync(f.file), type: MIME[path.extname(f.file)] || "font/woff2" };

  const blocked = [];
  const served = { runtime: 0, job: 0 };
  const summary = { tool: TOOL, ok: true, size: spec.size, fps: spec.fps, duration: spec.duration, beats };
  const tStart = process.hrtime.bigint();
  const browser = await chromium.launch({ args: ["--host-resolver-rules=MAP * ~NOTFOUND", "--disable-background-networking"] });
  let ff = null;
  try {
    const context = await browser.newContext({
      viewport: { width: spec.size[0], height: spec.size[1] },
      deviceScaleFactor: 1,
      serviceWorkers: "block",
      acceptDownloads: false,
      javaScriptEnabled: true,
    });
    await context.routeWebSocket(/.*/, (ws) => {
      blocked.push(`websocket ${ws.url()}`);
      ws.close();
    });
    await context.route("**/*", async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      if (url.origin !== ORIGIN || req.method() !== "GET") {
        blocked.push(`${req.method()} ${req.url()}`);
        return route.abort("blockedbyclient");
      }
      if (url.pathname === "/") return route.fulfill({ status: 200, contentType: "text/html", headers: { "content-security-policy": CSP }, body: pageHtml(fonts) });
      if (files[url.pathname]) {
        served.runtime++;
        return route.fulfill({ status: 200, contentType: files[url.pathname].type, body: files[url.pathname].body });
      }
      if (url.pathname.startsWith("/job/")) {
        const file = inside(jobDir, decodeURIComponent(url.pathname.slice(5)));
        const type = file && MIME[path.extname(file).toLowerCase()];
        if (file && type && fs.statSync(file).isFile()) {
          served.job++;
          return route.fulfill({ status: 200, contentType: type, body: fs.readFileSync(file) });
        }
      }
      blocked.push(`GET ${req.url()}`);
      return route.abort("blockedbyclient");
    });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e && e.message ? e.message : e)));
    await withTimeout(page.goto(`${ORIGIN}/`), 15000, "page load");
    if (errors.length || !(await page.evaluate(() => Boolean(window.__vc)))) throw new Error(`motion runtime did not start: ${errors.join(" | ") || blocked.join(" | ")}`);
    if (fonts.length) {
      const ok = await page.evaluate(
        async ({ family, weights }) => {
          await Promise.all(weights.map((w) => document.fonts.load(`${w} 40px "${family}"`)));
          return weights.filter((w) => document.fonts.check(`${w} 40px "${family}"`)).length;
        },
        { family: FONT_FAMILY, weights: fonts.map((f) => f.weight) },
      );
      if (ok < fonts.length) throw new Error(`brand fonts: ${ok} of ${fonts.length} loaded`);
    }
    const rel = path.relative(fs.realpathSync(jobDir), sceneFile).split(path.sep).map(encodeURIComponent).join("/");
    const info = await withTimeout(
      page.evaluate((u) => window.__vc.load(u), `/job/${rel}`),
      10000,
      "scene setup",
    );
    blocked.push(...info.blocked);
    summary.timeline_ms = info.duration_ms;
    const cdp = await context.newCDPSession(page);
    await cdp.send("Emulation.setDefaultBackgroundColorOverride", { color: { r: 0, g: 0, b: 0, a: 0 } });

    // --region-only: capture just the stage (plus a margin for shadows and overshoot) and
    // write a clip of that size; the summary says where it goes in the frame.
    let clip = null;
    if (args.regionOnly) {
      const m = Math.round(pageSpec.unit * 0.06);
      const [x0, y0, x1, y1] = spec.region.map(Math.round);
      const x = Math.max(0, x0 - m) & ~1;
      const y = Math.max(0, y0 - m) & ~1;
      clip = { x, y, width: (Math.min(spec.size[0], x1 + m) - x + 1) & ~1, height: (Math.min(spec.size[1], y1 + m) - y + 1) & ~1 };
    }
    const outSize = clip ? [clip.width, clip.height] : spec.size;
    const check = () => {
      const all = [...blocked, ...errors.map((e) => `error: ${e}`)];
      if (all.length) throw new Error(`sandbox: scene blocked or failed: ${all.slice(0, 5).join(" | ")}`);
    };
    const frameAt = async (t) => {
      const n = await withTimeout(page.evaluate((t) => window.__vc.seek(t), t), args.frameTimeout, `frame at ${t.toFixed(3)} s`);
      if (n) blocked.push(...(await page.evaluate(() => window.__vcBlocked.slice())));
      check();
      const shot = await withTimeout(
        cdp.send("Page.captureScreenshot", { format: "png", fromSurface: true, ...(clip ? { clip: { ...clip, scale: 1 } } : {}) }),
        args.frameTimeout,
        "frame capture",
      );
      return Buffer.from(shot.data, "base64");
    };
    check();

    // One sequential pass over the frame grid. Chromium keeps rasterised layers between
    // frames, so a picture depends (by a few anti-aliasing levels) on the frames drawn
    // before it; stills are therefore taken from the same pass as the clip, frame by
    // frame from 0, and are bit-identical to the clip's frames.
    const frames = Math.max(1, Math.round(spec.duration * spec.fps));
    const want = new Map();
    if (args.stillsDir) {
      fs.mkdirSync(args.stillsDir, { recursive: true });
      summary.stills = [];
      for (const t of args.stillTimes) {
        const i = Math.max(0, Math.min(frames - 1, Math.round(t * spec.fps)));
        const f = path.join(args.stillsDir, `t${t.toFixed(2).padStart(6, "0")}.png`);
        want.set(i, [...(want.get(i) || []), f]);
        summary.stills.push(f);
      }
    }
    const last = args.out ? frames - 1 : Math.max(...want.keys());
    if (args.out) ff = startFfmpeg(args.out, outSize, spec.fps);
    const r0 = process.hrtime.bigint();
    let drawMs = 0;
    let encMs = 0;
    for (let i = 0; i <= last; i++) {
      const a = process.hrtime.bigint();
      const png = await frameAt(i / spec.fps);
      drawMs += Number(process.hrtime.bigint() - a) / 1e6;
      for (const f of want.get(i) || []) fs.writeFileSync(f, png);
      const b = process.hrtime.bigint();
      if (ff) await ff.write(png);
      encMs += Number(process.hrtime.bigint() - b) / 1e6;
    }
    if (ff) {
      await ff.end();
      ff = null;
    }
    const secs = Number(process.hrtime.bigint() - r0) / 1e9;
    const n = last + 1;
    Object.assign(summary, {
      frames: n,
      render_seconds: Math.round(secs * 100) / 100,
      frames_per_second: Math.round((n / secs) * 10) / 10,
      ms_per_frame: { draw_and_capture: Math.round(drawMs / n), encode_wait: Math.round(encMs / n) },
    });
    if (args.out) Object.assign(summary, { out: args.out, out_size: outSize, place_at: clip ? [clip.x, clip.y] : [0, 0] });
    summary.requests = { ...served, blocked: blocked.length };
  } finally {
    if (ff) ff.kill();
    await Promise.race([browser.close(), new Promise((r) => setTimeout(r, 5000))]);
  }
  summary.seconds = Math.round(Number(process.hrtime.bigint() - tStart) / 1e7) / 100;
  return summary;
}

if (require.main === module)
  main()
    .then((s) => {
      console.log(JSON.stringify(s));
      process.exit(0);
    })
    .catch((e) => {
      if (!(e instanceof UsageError) && !String(e.message).startsWith("sandbox:")) console.error(e && e.stack ? e.stack : e);
      console.log(JSON.stringify({ tool: TOOL, ok: false, error: String(e && e.message ? e.message : e) }));
      process.exit(1);
    });

module.exports = { resolveBeats, CSP };
