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
      timeout: 60000,
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
