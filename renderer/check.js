#!/usr/bin/env node
// Self-test of the overlay chain: render N transparent canvas frames in headless
// Chromium and encode them as ProRes 4444 with alpha.
//   node check.js <out.mov> [frames=30] [width=1080] [height=1920]
const { spawn } = require("child_process");
const { chromium } = require("playwright-core");

const [out, frames = "30", w = "1080", h = "1920"] = process.argv.slice(2);
if (!out) {
  console.error("usage: node check.js <out.mov> [frames] [width] [height]");
  process.exit(2);
}

const PAGE = `<!doctype html><html><body style="margin:0;background:transparent">
<canvas id="c"></canvas><script>
const c = document.getElementById("c"), x = c.getContext("2d");
c.width = ${+w}; c.height = ${+h};
window.frame = (i, n) => {
  x.clearRect(0, 0, c.width, c.height);
  const t = i / Math.max(1, n - 1);
  x.fillStyle = "rgba(255,255,255,0.9)";
  x.beginPath();
  x.roundRect(c.width * (0.1 + 0.5 * t), c.height * 0.45, c.width * 0.3, c.height * 0.1, 24);
  x.fill();
  return c.toDataURL("image/png");
};
</script></body></html>`;

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: +w, height: +h } });
  await page.setContent(PAGE);
  const ff = spawn("ffmpeg", [
    "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", "30", "-c:v", "png", "-i", "-",
    "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le", out,
  ], { stdio: ["pipe", "inherit", "inherit"] });
  const done = new Promise((res, rej) =>
    ff.on("close", (code) => (code === 0 ? res() : rej(new Error(`ffmpeg exited ${code}`)))));
  for (let i = 0; i < +frames; i++) {
    const url = await page.evaluate(([i, n]) => window.frame(i, n), [i, +frames]);
    if (!ff.stdin.write(Buffer.from(url.split(",")[1], "base64")))
      await new Promise((r) => ff.stdin.once("drain", r));
  }
  ff.stdin.end();
  await done;
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
