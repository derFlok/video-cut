// Motion scenes (motion.js): the sandbox fails closed, frames are a pure function of
// time, beats resolve on spoken words, and scene modules may not name colours or fonts.
// Launches headless Chromium; skipped when the pinned browser is not installed.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");
const { resolveBeats } = require("../motion");
const { literalErrors } = require("../lib/literals");

const ROOT = path.join(__dirname, "..");
const FIX = path.join(__dirname, "motion");
const BROWSERS = process.env.PLAYWRIGHT_BROWSERS_PATH || path.join(ROOT, "..", ".tools", "ms-playwright");
const haveBrowser = fs.existsSync(BROWSERS);

function run(jobDir, spec, extra) {
  const file = path.join(jobDir, "motion.json");
  fs.writeFileSync(file, JSON.stringify(spec));
  let out;
  try {
    out = execFileSync(process.execPath, [path.join(ROOT, "motion.js"), file, ...extra], {
      env: { ...process.env, VIDEOCUT_JOB_DIR: jobDir, PLAYWRIGHT_BROWSERS_PATH: BROWSERS },
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
      timeout: 240000,
    });
  } catch (e) {
    out = e.stdout;
  }
  return JSON.parse(out.trim().split("\n").pop());
}

const base = () => JSON.parse(fs.readFileSync(path.join(FIX, "gallery.json"), "utf8"));

function job(sceneSrc) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vc-motion-"));
  fs.writeFileSync(path.join(dir, "scene.js"), sceneSrc);
  return dir;
}

function hostile(name, body) {
  test(`sandbox: a scene that ${name} fails closed`, { skip: !haveBrowser && "no browser" }, () => {
    const dir = job(`export default function scene(vc) {\n${body}\n}\n`);
    const r = run(dir, { ...base(), scene: "scene.js", duration: 0.2 }, ["--stills", path.join(dir, "s"), "0.1"]);
    assert.equal(r.ok, false, JSON.stringify(r));
    assert.match(r.error, /sandbox|blocked|not available|Refused|import/i);
    assert.ok(!fs.existsSync(path.join(dir, "s", "t000.10.png")), "no frame was written");
  });
}

hostile("calls fetch", `fetch("http://example.com/x").catch(() => {});`);
hostile("swallows a fetch error", `try { fetch("http://example.com/x"); } catch (e) {}`);
hostile("uses XMLHttpRequest", `const x = new XMLHttpRequest(); x.open("GET", "http://example.com/"); x.send();`);
hostile("opens a WebSocket", `new WebSocket("ws://example.com/");`);
hostile("imports a module over http", `import("http://example.com/m.js").catch(() => {});`);
hostile("loads an image over https", `const i = document.createElement("img"); i.src = "https://example.com/p.png"; document.body.appendChild(i);`);
hostile("loads a stylesheet background over https", `const d = document.createElement("div"); d.style.backgroundImage = "url(https://example.com/p.png)"; d.style.width = "10px"; d.style.height = "10px"; document.body.appendChild(d);`);
hostile("reads a file outside the job folder", `const i = document.createElement("img"); i.src = "/job/../../etc/hosts"; document.body.appendChild(i);`);
hostile("opens a WebRTC connection by its legacy name", `new window.webkitRTCPeerConnection({ iceServers: [{ urls: "turn:192.0.2.1", username: "x", credential: "y" }] });`);
hostile("hides what it tried by emptying the blocked list", `try { fetch("https://example.com"); } catch (e) {} window.__vcBlocked.length = 0; window.__vcBlocked = [];`);
hostile("never returns from a frame", `vc.onSeek((t) => { if (t > 0.05) for (;;) {} });`);

test("sandbox: a scene module outside the job folder is refused", { skip: !haveBrowser && "no browser" }, () => {
  const dir = job("export default function scene(vc) {}\n");
  const r = run(dir, { ...base(), scene: "../outside.js" }, ["--stills", path.join(dir, "s"), "0.1"]);
  assert.equal(r.ok, false);
  assert.match(r.error, /inside the job folder/);
});

test("a scene that names a colour or a font is refused before it runs", () => {
  assert.deepEqual(literalErrors(`el.style.background = "var(--vc-accent)";`), []);
  assert.ok(literalErrors(`el.style.color = "#ff0000";`).length);
  assert.ok(literalErrors(`el.style.color = "rgb(1, 2, 3)";`).length);
  assert.ok(literalErrors(`el.style.color = "white";`).length);
  assert.ok(literalErrors(`el.style.fontFamily = "Helvetica";`).length);
  assert.ok(literalErrors(`vc.css(".x { font-family: serif }")`).length);
});

test("beats resolve on the n-th spoken word or phrase, with an offset", () => {
  const words = [
    { w: "Zwei,", t0: 1, t1: 1.2 },
    { w: "drei", t0: 1.3, t1: 1.5 },
    { w: "zwei", t0: 2, t1: 2.2 },
    { w: "Jahre.", t0: 2.2, t1: 2.6 },
  ];
  assert.deepEqual(resolveBeats({ a: { word: "zwei", n: 2 }, b: { word: "zwei Jahre", edge: "end" }, c: { word: "drei", offset: -0.1 } }, words), {
    a: 2,
    b: 2.6,
    c: 1.2,
  });
  assert.throws(() => resolveBeats({ x: { word: "vier" } }, words), /not spoken/);
});

test("frames are a pure function of time and stills are the clip's frames", { skip: !haveBrowser && "no browser" }, () => {
  const dir = job(fs.readFileSync(path.join(FIX, "gallery.js"), "utf8") + "\nexport const r = Math.random() + Date.now();\n");
  const spec = { ...base(), scene: "scene.js" };
  const a = run(dir, spec, ["--stills", path.join(dir, "a"), "2.35"]);
  const b = run(dir, spec, ["--stills", path.join(dir, "b"), "2.9,0.2,1.1,2.35", "--out", path.join(dir, "b.mov")]);
  assert.ok(a.ok && b.ok, JSON.stringify([a, b]));
  const pa = fs.readFileSync(path.join(dir, "a", "t002.35.png"));
  const pb = fs.readFileSync(path.join(dir, "b", "t002.35.png"));
  assert.ok(pa.equals(pb), "the same time gives the same picture");
  assert.equal(a.requests.blocked, 0);
  // two full renders give the same frames
  const c = run(dir, spec, ["--out", path.join(dir, "c.mov")]);
  assert.ok(c.ok);
  const md5 = (f) => execFileSync("ffmpeg", ["-loglevel", "error", "-i", f, "-f", "framemd5", "-"], { encoding: "utf8" }).replace(/^#.*\n/gm, "");
  assert.equal(md5(path.join(dir, "b.mov")), md5(path.join(dir, "c.mov")));
});

test("the gallery renders an alpha ProRes clip", { skip: !haveBrowser && "no browser" }, () => {
  const dir = job(fs.readFileSync(path.join(FIX, "gallery.js"), "utf8"));
  const out = path.join(dir, "g.mov");
  const r = run(dir, { ...base(), scene: "scene.js", duration: 0.5 }, ["--out", out]);
  assert.ok(r.ok, JSON.stringify(r));
  assert.equal(r.frames, 15);
  assert.ok(fs.statSync(out).size > 1000);
});

// --- motion design: brand style, cues, 3D -----------------------------------------------

const DARK = {
  colors: { bg: "#0B0C10", surface: "#151821", text: "#F2F4F8", muted: "#8A93A6", accent: "#20242C" },
  fonts: { weights: [400, 800], files: {} },
  motion: { spring: { w: 14, z: 0.8 }, theme: "auto", energy: "punchy" },
  caption_colors: { box: "#151821", text: "#F2F4F8", highlight: "#F2F4F8" },
};

test("cues: --events writes sorted cues in output seconds (scene t + offset)", { skip: !haveBrowser && "no browser" }, () => {
  const dir = job(fs.readFileSync(path.join(FIX, "gallery.js"), "utf8"));
  const ev = path.join(dir, "events.json");
  const r = run(dir, { ...base(), scene: "scene.js", offset: 10 }, ["--events", ev]);
  assert.ok(r.ok, JSON.stringify(r));
  const cues = JSON.parse(fs.readFileSync(ev, "utf8"));
  assert.ok(cues.length >= 10);
  const kinds = new Set(["pop", "whoosh", "tick", "check", "strike", "type", "count", "impact", "swoosh_out"]);
  for (const c of cues) {
    assert.deepEqual(Object.keys(c).sort(), ["component", "kind", "strength", "t"]);
    assert.ok(kinds.has(c.kind), c.kind);
    assert.ok(c.t >= 10 && c.t <= 13, `t ${c.t} in output seconds`);
    assert.ok(c.strength >= 0 && c.strength <= 1);
  }
  assert.deepEqual(cues.map((c) => c.t), cues.map((c) => c.t).slice().sort((a, b) => a - b));
  assert.ok(cues.some((c) => c.kind === "check") && cues.some((c) => c.kind === "strike") && cues.some((c) => c.kind === "count"));
});

test("cues: an unknown cue kind fails the render", { skip: !haveBrowser && "no browser" }, () => {
  const dir = job(`export default function scene(vc) { vc.cue("boom", 0.5); }\n`);
  const r = run(dir, { ...base(), scene: "scene.js", duration: 0.2 }, ["--stills", path.join(dir, "s"), "0.1"]);
  assert.equal(r.ok, false);
  assert.match(r.error, /cue kind/);
});

test("style: theme auto follows the brand background, depth and contrast are derived", { skip: !haveBrowser && "no browser" }, () => {
  const dir = job(`export default function scene(vc) { vc.c.stat(vc.c.slot(vc.stage), { value: 3, at: 0.1 }); }\n`);
  const dark = run(dir, { ...base(), scene: "scene.js", duration: 0.2, tokens: DARK }, ["--stills", path.join(dir, "d"), "0.1"]);
  assert.ok(dark.ok, JSON.stringify(dark));
  assert.equal(dark.style.theme, "dark");
  assert.equal(dark.style.depth, "glow");
  assert.equal(dark.style.energy, "punchy");
  // the accent is nearly the panel colour: accent text and text on the accent are guarded
  assert.ok(dark.style.contrast.accent_text_on_panel >= 4.5, JSON.stringify(dark.style));
  assert.ok(dark.style.contrast.on_accent >= 4.5, JSON.stringify(dark.style));
  assert.ok(dark.style.contrast.ink_on_panel >= 7);
  const light = run(dir, { ...base(), scene: "scene.js", duration: 0.2 }, ["--stills", path.join(dir, "l"), "0.1"]);
  assert.equal(light.style.theme, "light");
  assert.equal(light.style.depth, "soft");
  const forced = run(dir, { ...base(), scene: "scene.js", duration: 0.2, tokens: { ...base().tokens, motion: { theme: "dark", depth: "flat" } } }, ["--stills", path.join(dir, "f"), "0.1"]);
  assert.equal(forced.style.theme, "dark");
  assert.equal(forced.style.depth, "flat");
});

test("3D: a Three.js field and an extruded number render through onSeek (no rAF)", { skip: !haveBrowser && "no browser" }, () => {
  const dir = job(`export default function scene(vc) {
  vc.c.field3d(vc.c.slot(vc.stage, { fit: false }), { at: 0, kind: "particles", count: 300 });
  vc.c.number3d(vc.c.slot(vc.stage, { fit: false }), { text: "7", at: 0.05 });
}\n`);
  const r = run(dir, { ...base(), scene: "scene.js", duration: 0.4, region: [60, 200, 1020, 800] }, ["--stills", path.join(dir, "s"), "0.3", "--out", path.join(dir, "a.mov")]);
  assert.ok(r.ok, JSON.stringify(r));
  assert.equal(r.requests.blocked, 0);
  const png = fs.readFileSync(path.join(dir, "s", "t000.30.png"));
  assert.ok(png.length > 5000, "the WebGL canvas drew something");
  const r2 = run(dir, { ...base(), scene: "scene.js", duration: 0.4, region: [60, 200, 1020, 800] }, ["--out", path.join(dir, "b.mov")]);
  assert.ok(r2.ok);
  const md5 = (f) => execFileSync("ffmpeg", ["-loglevel", "error", "-i", f, "-f", "framemd5", "-"], { encoding: "utf8" }).replace(/^#.*\n/gm, "");
  assert.equal(md5(path.join(dir, "a.mov")), md5(path.join(dir, "b.mov")), "SwiftShader 3D frames are deterministic");
});

test("the component gallery and the brand-sheet scene use brand tokens only and render", { skip: !haveBrowser && "no browser" }, () => {
  for (const f of [path.join(FIX, "gallery_full.js"), path.join(ROOT, "motion", "sheet_scene.js")])
    assert.deepEqual(literalErrors(fs.readFileSync(f, "utf8")), [], f);
  const dir = job(fs.readFileSync(path.join(FIX, "gallery_full.js"), "utf8"));
  // the first 8 slots (3D field, stat, charts, comparison, steps, table, browser frame)
  const times = Array.from({ length: 8 }, (_, i) => (i * 2.6 + 2.2).toFixed(2)).join(",");
  const r = run(dir, { ...base(), scene: "scene.js", duration: 20.8, region: [40, 200, 1040, 900], tokens: DARK }, ["--region-only", "--stills", path.join(dir, "s"), times, "--events", path.join(dir, "e.json")]);
  assert.ok(r.ok, JSON.stringify(r));
  assert.equal(r.stills.length, 8);
  assert.ok(r.cues > 40, String(r.cues));
});

test("every component in CATALOGUE.md exists, and every component is in the catalogue", () => {
  const cat = fs.readFileSync(path.join(ROOT, "motion", "CATALOGUE.md"), "utf8");
  const listed = [...cat.slice(cat.indexOf("## Components")).matchAll(/^\| `(\w+)` \|/gm)].map((m) => m[1]);
  const src = fs.readFileSync(path.join(ROOT, "motion", "components.js"), "utf8");
  const ret = src.slice(src.lastIndexOf("return {"));
  const exported = ret.slice(ret.indexOf("// components") + 13).match(/\w+/g);
  assert.deepEqual(listed.slice().sort(), exported.slice().sort());
});
