// Screenshots of every screen against the mock build (npm run build:mock first).
// Reuses the renderer's playwright-core and its installed Chromium; downloads nothing.
//   node scripts/screenshots.mjs <out-dir>

import { createReadStream, existsSync, mkdirSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL("..", import.meta.url));
const dist = join(here, "dist-mock");
const out = resolve(process.argv[2] ?? join(here, "screenshots"));
if (!existsSync(join(dist, "index.html"))) {
  console.error("dist-mock missing: run `npm run build:mock` first");
  process.exit(1);
}
mkdirSync(out, { recursive: true });

const require = createRequire(join(here, "..", "renderer", "package.json"));
const { chromium } = require("playwright-core");

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const server = createServer((req, res) => {
  const path = normalize(decodeURIComponent(new URL(req.url, "http://x").pathname)).replace(/^([/\\])+/, "");
  let file = join(dist, path || "index.html");
  if (!file.startsWith(dist) || !existsSync(file) || statSync(file).isDirectory()) file = join(dist, "index.html");
  res.writeHead(200, { "Content-Type": TYPES[extname(file)] ?? "application/octet-stream" });
  createReadStream(file).pipe(res);
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}/`;

const SHOTS = [
  ["01-home", "#/"],
  ["02-new-job", "#/new", async (p) => {
    await p.getByText("Produkt-Walkthrough", { exact: true }).click();
    await p.getByText("16:9", { exact: true }).click();
  }],
  ["03-progress", "#/jobs/job-running", async (p) => p.getByRole("button", { name: /Protokoll anzeigen/ }).click()],
  ["04-plan-review", "#/jobs/job-approval"],
  ["05-plan-feedback", "#/jobs/job-approval", async (p) => {
    await p.getByLabel("Was soll Claude am Plan ändern?").fill("Die Zwischenfrage weglassen, das Beispiel länger");
    await p.getByRole("button", { name: "An Claude schicken" }).click();
    await p.locator(".banner-change").waitFor({ timeout: 15000 });
  }],
  ["06-personal-data", "#/jobs/job-approval", async (p) => {
    await p.getByRole("button", { name: "Plan freigeben und schneiden" }).click();
    await p.getByRole("dialog").waitFor();
  }],
  ["07-result", "#/jobs/job-done"],
  ["08-brands", "#/brands"],
  ["09-brand-editor", "#/brands/dark"],
  ["10-error-job", "#/jobs/job-error"],
  ["11-overlays-running", "#/jobs/job-overlays"],
  ["12-recut", "#/jobs/job-done", async (p) => {
    await p.getByRole("combobox", { name: "Schnittstil" }).selectOption("calm");
    await p.getByRole("button", { name: "Neu schneiden" }).waitFor();
  }],
];

const browser = await chromium.launch();
const written = [];
try {
  for (const [vw, vh, tag] of [[1280, 900, "desktop"], [390, 844, "phone"]]) {
    for (const scheme of ["light", "dark"]) {
      const ctx = await browser.newContext({ viewport: { width: vw, height: vh }, colorScheme: scheme, deviceScaleFactor: tag === "phone" ? 2 : 1 });
      for (const [name, hash, act] of SHOTS) {
        const page = await ctx.newPage();
        await page.goto(base + hash);
        await page.locator("h1").first().waitFor();
        await page.waitForTimeout(400);
        if (act) await act(page);
        await page.waitForTimeout(300);
        if (!name.includes("personal-data")) await page.evaluate(() => window.scrollTo(0, 0));
        const file = join(out, `${name}-${tag}-${scheme}.png`);
        // A modal is fixed to the viewport; a full-page capture would tear it.
        await page.screenshot({ path: file, fullPage: !name.includes("personal-data") });
        written.push(file);
        await page.close();
      }
      await ctx.close();
    }
  }
} finally {
  await browser.close();
  server.close();
}
console.log(`${written.length} screenshots in ${out}`);
