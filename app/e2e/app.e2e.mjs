// End-to-end run of the local app through the browser only: a REAL server and REAL Claude
// runs on generated clips. Started by tests/e2e/app_e2e.py (which makes the clips, starts
// `videocut ui` in a throwaway VIDEOCUT_HOME and reports the numbers); not part of CI.
// Reuses the renderer's playwright-core and its installed Chromium; downloads nothing.
//
//   node app/e2e/app.e2e.mjs run --url <http://127.0.0.1:P/?t=TOKEN> --camera <file>
//        [--screen <file>] --out <screenshot dir>
//   node app/e2e/app.e2e.mjs card <out.png>   (a fake contact card for the screen clip)
//   node app/e2e/app.e2e.mjs look --url <...> --job <id> --out <dir>   (read-only visit)
//
// Prints one JSON line at the end: {"job": id, "steps": [...], "ok": true}.

import { mkdirSync, readFileSync, statSync } from "node:fs";
import { createRequire } from "node:module";
import { basename, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = fileURLToPath(new URL("..", import.meta.url));
const require = createRequire(join(here, "..", "renderer", "package.json"));
const { chromium } = require("playwright-core");

const MIN = 60_000;
const args = Object.fromEntries(
  process.argv.slice(3).reduce((acc, a, i, all) => (a.startsWith("--") ? [...acc, [a.slice(2), all[i + 1]]] : acc), []),
);
const cmd = process.argv[2];
const steps = [];
const t0 = Date.now();
const log = (msg) => console.error(`[${((Date.now() - t0) / 1000).toFixed(0).padStart(5)}s] ${msg}`);

async function browser() {
  const b = await chromium.launch();
  const baseURL = args.url ? new URL(args.url).origin : undefined;
  const ctx = await b.newContext({ baseURL, viewport: { width: 1440, height: 900 }, colorScheme: "light", locale: "de-DE", acceptDownloads: true });
  return { b, ctx, page: await ctx.newPage() };
}

let shotNo = 0;
async function shot(page, name) {
  const file = join(args.out, `${String(++shotNo).padStart(2, "0")}-${name}.png`);
  // the sticky top bar would land in the middle of a full-page picture
  await page.addStyleTag({ content: "header{position:static!important}" }).catch(() => null);
  await page.screenshot({ path: file, fullPage: true });
  steps.push({ step: name, at_s: Math.round((Date.now() - t0) / 1000) });
  log(`screenshot ${basename(file)}`);
}

/** Poll the job (same cookie as the page) until `ok(job)`; fail on an error state. */
async function waitJob(page, id, ok, what, timeout = 40 * MIN) {
  const end = Date.now() + timeout;
  let last = "";
  for (;;) {
    const r = await page.request.get(`/api/jobs/${id}`);
    const j = await r.json();
    const now = `${j.status}/${j.stage}/${j.gate}`;
    if (now !== last) log(`${what}: ${now}`);
    last = now;
    if (ok(j)) return j;
    if (j.status === "error") throw new Error(`${what}: the run failed: ${j.error}`);
    if (Date.now() > end) throw new Error(`${what}: timed out at ${now}`);
    await page.waitForTimeout(3000);
  }
}

/** Drop a file onto the drop zone whose title is `label` (a real drag & drop event). */
async function dropFile(page, label, path, mime) {
  const b64 = readFileSync(path).toString("base64");
  const zone = page.locator(".dropzone").filter({ hasText: label }).first();
  const dt = await page.evaluateHandle(
    ({ b64, name, mime }) => {
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      const dt = new DataTransfer();
      dt.items.add(new File([bytes], name, { type: mime }));
      return dt;
    },
    { b64, name: basename(path), mime },
  );
  await zone.dispatchEvent("dragover", { dataTransfer: dt });
  await zone.dispatchEvent("drop", { dataTransfer: dt });
}

async function card(out) {
  // Fake contact data in the style of a CRM detail pane: the placeholder names and the
  // example.com address are documentation values, not real people.
  const { b, page } = await browser();
  await page.setViewportSize({ width: 1400, height: 520 });
  await page.setContent(`<body style="margin:0;background:#fff;font:600 54px system-ui,sans-serif;color:#111">
    <div style="padding:40px 56px;border:6px solid #888;height:420px;box-sizing:border-box">
      <div style="font-size:40px;color:#666">Kontakt</div>
      <div style="margin-top:18px">Erika Mustermann</div>
      <div style="margin-top:18px">erika.mustermann@example.com</div>
      <div style="margin-top:18px">+49 30 1234567</div>
    </div></body>`);
  await page.screenshot({ path: out });
  await b.close();
}

async function run() {
  mkdirSync(args.out, { recursive: true });
  const { b, page } = await browser();
  const reelResponses = [];
  page.on("response", (r) => {
    if (/\/media\/(reel|history\/)/.test(r.url())) reelResponses.push({ url: r.url(), status: r.status() });
  });
  try {
    await page.goto(args.url); // ?t= is exchanged for the session cookie, then redirected
    await page.getByRole("link", { name: "Neues Video" }).first().waitFor();
    await shot(page, "home");

    // 1. new job: type, camera by drag & drop, screen recording by the file picker
    await page.getByRole("link", { name: "Neues Video" }).first().click();
    await page.getByText(args.screen ? "Produkt-Walkthrough" : "Gedanke des Tages", { exact: true }).click();
    await dropFile(page, "Kamera-Aufnahme", args.camera, "video/mp4");
    await page.getByText(basename(args.camera)).waitFor();
    if (args.screen) {
      const input = page.locator(".dropzone").filter({ hasText: "Bildschirmaufnahme" }).locator('input[type="file"]');
      await input.setInputFiles(args.screen);
      await page.getByText(basename(args.screen)).waitFor();
    }
    await page.locator("#brand").selectOption("_example");
    await page.locator("#brief").fill("Kurz und klar, die Übersicht in drei Schritten.");
    await shot(page, "new-job");
    await page.getByRole("button", { name: "Hochladen und starten" }).click();
    await page.waitForURL(/#\/jobs\//, { timeout: 10 * MIN });
    const id = decodeURIComponent(page.url().split("#/jobs/")[1]);
    log(`job ${id}`);

    // 2. live progress
    await page.getByText(/Protokoll/).first().waitFor();
    await page.waitForTimeout(4000);
    const showLog = page.getByRole("button", { name: /Protokoll anzeigen/ });
    if (await showLog.count()) await showLog.first().click();
    await shot(page, "progress-prep");
    await waitJob(page, id, (j) => j.stage === "plan" && j.status === "running", "plan starts", 20 * MIN);
    await page.waitForTimeout(20_000);
    await shot(page, "progress-plan");

    // 3. the plan (plan.json + stills)
    await waitJob(page, id, (j) => j.gate === "plan", "plan gate");
    await page.reload();
    await page.getByRole("button", { name: "Plan freigeben und schneiden" }).waitFor();
    await page.waitForTimeout(1500);
    const stills = await page.locator('img[src*="/stills/"]').count();
    log(`plan stills shown: ${stills}`);
    await shot(page, "plan");

    // 4. plan feedback once
    await page.getByLabel("Was soll Claude am Plan ändern?").fill("Den Schluss kürzer machen, der letzte Satz reicht als Abschluss.");
    await shot(page, "plan-feedback-typed");
    await page.getByRole("button", { name: "An Claude schicken" }).click();
    await page.waitForTimeout(8000);
    await shot(page, "plan-revising");
    await waitJob(page, id, (j) => j.gate === "plan" && j.status === "waiting", "plan revised");
    await page.waitForTimeout(3000);
    await page.getByRole("button", { name: "Plan freigeben und schneiden" }).waitFor();
    await shot(page, "plan-revised");

    // 5. approve; the personal-data dialog when the plan found some
    const before = await (await page.request.get(`/api/jobs/${id}`)).json();
    const candidates = before.redaction?.candidates?.length ?? 0;
    log(`personal-data candidates: ${candidates}`);
    await page.getByRole("button", { name: "Plan freigeben und schneiden" }).click();
    if (candidates > 0) {
      const dialog = page.getByRole("dialog");
      await dialog.waitFor();
      await page.waitForTimeout(1000);
      await shot(page, "personal-data");
      // the human decision: redact one item, keep the others (choose per item)
      await dialog.getByText("Einzeln wählen").click();
      const boxes = dialog.getByRole("checkbox");
      const n = await boxes.count();
      for (let i = 1; i < n; i++) if (await boxes.nth(i).isChecked()) await boxes.nth(i).uncheck();
      if (!(await boxes.nth(0).isChecked())) await boxes.nth(0).check();
      await shot(page, "personal-data-decided");
      await dialog.getByRole("button", { name: "Freigeben" }).click();
    }
    await page.waitForTimeout(8000);
    await shot(page, "cut-running");
    const approved = await (await page.request.get(`/api/jobs/${id}`)).json();
    steps.push({ step: "decision", redaction: approved.redaction?.decision ?? null });
    await waitJob(page, id, (j) => j.stage === "overlays" && j.status === "running", "overlays start", 60 * MIN).catch(() => null);
    await shot(page, "overlays-running");

    // 6. result: the player plays (Range requests)
    await waitJob(page, id, (j) => j.gate === "result" && j.status === "done", "result", 60 * MIN);
    await page.reload();
    const video = page.locator(".player video");
    await video.waitFor();
    await video.evaluate((v) => ((v.muted = true), v.play()));
    await page.waitForFunction(() => document.querySelector(".player video")?.currentTime > 1.5, null, { timeout: MIN });
    const ranged = reelResponses.filter((r) => r.status === 206).length;
    log(`player plays; ${ranged} partial (206) media responses`);
    if (!ranged) throw new Error("the player got no 206 response (Range)");
    await video.evaluate((v) => v.pause());
    await shot(page, "result");

    // 7. feedback at the current position
    const duration = await video.evaluate((v) => v.duration);
    const at = Math.round(duration * 0.8);
    await video.evaluate((v, t) => (v.currentTime = t), at);
    await page.waitForTimeout(800);
    await page.getByLabel("Was soll anders werden?").fill("Diesen Satz hier bitte ganz rausnehmen, sonst bleibt alles so.");
    await page.getByLabel(/bei aktueller Stelle/).check();
    await shot(page, "feedback-typed");
    await page.getByRole("button", { name: "Korrektur schicken" }).click();
    await page.waitForTimeout(8000);
    await shot(page, "revise-running");
    const done = await waitJob(page, id, (j) => j.gate === "result" && j.status === "done" && j.revisions.length > 0, "revision", 60 * MIN);
    const rev = done.revisions.at(-1);
    steps.push({ step: "revision", at_s: rev.at_s, ok: rev.ok, changed: rev.changed, cost: rev.cost, turns: rev.turns, wall_s: rev.wall_s });
    if (rev.ok === false) throw new Error(`the revision failed: ${rev.error}`);

    // 8. the new version is listed and the old one plays
    await page.reload();
    await page.getByText("Version 2").first().waitFor();
    await shot(page, "versions");
    await page.getByRole("button", { name: "Abspielen: Version 1 · erster Schnitt" }).or(page.getByRole("button", { name: /Abspielen: Version 1/ })).first().click();
    await page.waitForFunction(() => document.querySelector(".player video")?.dataset.version === "1");
    await video.evaluate((v) => ((v.muted = true), v.play()));
    await page.waitForFunction(() => document.querySelector(".player video")?.currentTime > 1, null, { timeout: MIN });
    await video.evaluate((v) => v.pause());
    await shot(page, "version-1-playing");

    // 9. download
    const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "MP4 herunterladen" }).click()]);
    const file = join(args.out, download.suggestedFilename());
    await download.saveAs(file);
    const size = statSync(file).size;
    log(`download ${basename(file)}: ${size} bytes`);
    if (size < 10_000) throw new Error("the download is too small");
    steps.push({ step: "download", name: basename(file), bytes: size });
    console.log(JSON.stringify({ job: id, steps, ok: true }));
  } catch (e) {
    await shot(page, "failure").catch(() => null);
    console.log(JSON.stringify({ steps, ok: false, error: String(e?.stack ?? e) }));
    process.exitCode = 1;
  } finally {
    await b.close();
  }
}

/** Read-only visit of an existing job: no button that starts a run is pressed. */
async function look() {
  mkdirSync(args.out, { recursive: true });
  const { b, page } = await browser();
  const writes = [];
  page.on("request", (r) => {
    if (r.method() !== "GET") writes.push(`${r.method()} ${r.url()}`);
  });
  try {
    await page.goto(args.url);
    await page.goto(`${new URL(args.url).origin}/#/jobs/${encodeURIComponent(args.job)}`);
    await page.locator(".player video").waitFor({ timeout: MIN });
    await page.waitForTimeout(2000);
    const versions = await page.locator(".history li").count();
    steps.push({ step: "result", versions });
    await shot(page, "look-result");
    await page.getByText("Den freigegebenen Plan ansehen").click();
    await page.getByRole("heading", { name: "Der Plan" }).waitFor({ timeout: MIN });
    await page.waitForTimeout(1500);
    const structured = await page.locator(".plan").count();
    steps.push({ step: "plan", structured: structured > 0 });
    await page.getByRole("heading", { name: "Der Plan" }).scrollIntoViewIfNeeded();
    await shot(page, "look-plan");
    if (writes.length) throw new Error(`the visit sent writes: ${writes.join(", ")}`);
    console.log(JSON.stringify({ job: args.job, steps, ok: true }));
  } catch (e) {
    console.log(JSON.stringify({ steps, ok: false, error: String(e?.stack ?? e) }));
    process.exitCode = 1;
  } finally {
    await b.close();
  }
}

if (cmd === "card") await card(resolve(process.argv[3]));
else if (cmd === "run") await run();
else if (cmd === "look") await look();
else {
  console.error("usage: app.e2e.mjs run|card|look ...");
  process.exitCode = 2;
}
