// Output formats: every scene lays out from the frame's width/height and aspect class.
// All four presets (9:16, 16:9, 1:1, 4:5): drawn boxes inside the frame and the safe
// zones, text inside its box (not clipped), no overlap between a caption and a card that
// are on screen together, and the landscape placement rules.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");
const { overlapArea, allowedArea } = require("../lib/layout");
const { union } = require("../lib/boxes");
const style = require("../lib/style");
const { PRESETS, tokens, fakeCtx, makeEnv, faceBox, pipBox, SAMPLES, captionItem } = require("./formats_env");

const scene = (n) => require(path.join(__dirname, "..", "scenes", `${n}.js`));
const FPS = 30;
const SHOTS = { landscape: { width: 1600, height: 1000 }, portrait: { width: 1170, height: 2532 } };
const IMAGES = { "shot.png": SHOTS.landscape, "phone.png": SHOTS.portrait };
const CARDS = Object.keys(SAMPLES).filter((n) => !["keyword", "cta_end"].includes(n));

const itemOf = (n, props = SAMPLES[n], t0 = 0, t1 = 3) => ({ scene: n, t0, t1, props });

// Union of everything an item draws over its life.
function drawAll(mod, item, env, ctx) {
  let box = null;
  for (let i = 0; i <= Math.round((item.t1 - item.t0) * FPS); i++) box = union(box, mod.draw(ctx, i / FPS, item, env));
  return box;
}

test("the frame helper: aspect classes, short-side unit, 1080x1920 when absent", () => {
  assert.deepEqual(style.frame({}), { w: 1080, h: 1920, unit: 1080, aspect: "portrait", landscape: false, tall: true });
  const cases = { "9:16": "portrait", "16:9": "landscape", "1:1": "square", "4:5": "portrait" };
  for (const [p, aspect] of Object.entries(cases)) {
    const f = style.frame(makeEnv(p));
    assert.equal(f.aspect, aspect, p);
    assert.equal(f.unit, 1080, p);
  }
  assert.equal(style.frame({ size: [1920, 1080], width: 1280, height: 720 }).w, 1280);
  assert.equal(style.frame({ width: 1000, height: 1100 }).aspect, "square");
});

for (const preset of Object.keys(PRESETS)) {
  const { size, safe } = PRESETS[preset];
  const [W, H] = size;
  const area = allowedArea(size, safe);
  const faces = { face: faceBox(size), "no face": null };
  // A landscape screen layout carries the speaker as a bubble in the bottom-right corner.
  if (preset === "16:9") faces.pip = pipBox(size, safe);

  for (const [faceName, face] of Object.entries(faces))
    for (const n of [...CARDS, "keyword", "cta_end"]) {
      test(`${preset} ${n} (${faceName}): inside the frame and safe zones, text not clipped`, () => {
        const env = makeEnv(preset, { face, images: IMAGES });
        const mod = scene(n);
        const item = itemOf(n, SAMPLES[n], 1, 4);
        const ctx = fakeCtx();
        const L = mod.layout(ctx, item, env);
        assert.ok(L && L.fits, "fits");
        // A stage card (a screenshot in a landscape frame) may cover the speaker. A square
        // or 4:5 frame with a head in the middle can run out of room for a card, a
        // caption and the head together: then the layout says so (overlapsFace) and the
        // renderer reports it, never silently.
        const cramped = (preset === "1:1" || preset === "4:5") && !!face;
        if (!L.stage && !cramped) assert.equal(L.overlapsFace, false);
        const box = drawAll(mod, item, env, ctx);
        assert.ok(box && box[0] >= 0 && box[1] >= 0 && box[2] <= W && box[3] <= H, `inside the frame: ${box}`);
        if (n === "cta_end") {
          assert.deepEqual(box, [0, 0, W, H]);
          // Content centred and inside the safe area.
          const [ax0, ay0, ax1, ay1] = area;
          assert.ok(ctx.texts.length > 0);
          for (const tx of ctx.texts) {
            assert.ok(tx.x0 >= ax0 && tx.x1 <= ax1, `cta text ${tx.x0}..${tx.x1}`);
            assert.ok(Math.abs((tx.x0 + tx.x1) / 2 - W / 2) < 1, "cta text centred");
            assert.ok(tx.y - tx.size / 2 >= ay0 && tx.y + tx.size / 2 <= ay1, "cta text vertically in the safe area");
          }
          if (style.frame(env).landscape) assert.ok(Math.max(...ctx.texts.map((x) => x.x1 - x.x0)) <= W * 0.7 + 1);
          return;
        }
        const [ax0, ay0, ax1, ay1] = area;
        assert.ok(box[0] >= ax0 && box[1] >= ay0 && box[2] <= ax1 && box[3] <= ay1, `inside the safe zones: ${box} vs ${area}`);
        if (face && !L.stage && !(cramped && L.overlapsFace)) assert.equal(overlapArea(box, face), 0, `clear of the face: ${box}`);
        // Text stays inside the laid-out box (a screenshot has no text of its own).
        for (const tx of ctx.texts) {
          assert.ok(
            tx.x0 >= L.box[0] - 1 && tx.x1 <= L.box[2] + 1 && tx.y - tx.size / 2 >= L.box[1] - 1 && tx.y + tx.size / 2 <= L.box[3] + 1,
            `"${tx.text}" ${tx.x0}..${tx.x1} outside ${L.box}`,
          );
        }
      });
    }

  for (const [faceName, face] of Object.entries(faces)) {
    test(`${preset} caption (${faceName}): two lines at most, inside the safe zones, clear of the face`, () => {
      const env = makeEnv(preset, { face });
      const item = captionItem();
      const ctx = fakeCtx();
      const L = scene("caption").layout(ctx, item, env);
      assert.ok(L.fits && !L.overlapsFace);
      assert.ok(L.lines.length <= 2);
      const box = drawAll(scene("caption"), item, env, ctx);
      const [ax0, ay0, ax1, ay1] = area;
      assert.ok(box[0] >= ax0 && box[1] >= ay0 && box[2] <= ax1 && box[3] <= ay1, `${box} vs ${area}`);
      if (face) assert.equal(overlapArea(box, face), 0);
      for (const tx of ctx.texts) assert.ok(tx.x0 >= L.box[0] - 1 && tx.x1 <= L.box[2] + 1, "caption text inside its block");
      assert.ok(L.px <= Math.min(W, H) * 0.07 + 1, "type scale follows the short side");
      if (preset === "16:9") {
        assert.ok(L.width <= W * 0.7, `line width ${L.width} <= 70 % of ${W}`);
        assert.ok(Math.abs((L.box[0] + L.box[2]) / 2 - W / 2) < 1, "centred");
        if (!face) assert.ok(L.box[3] >= ay1 - 2 * L.reach, `at the bottom: ${L.box}`);
        else assert.ok(L.box[3] > H / 2, "below the middle");
      }
    });

    // A caption and a card on screen together never overlap, whichever is laid out first.
    for (const n of CARDS)
      for (const order of ["card first", "caption first"]) {
        test(`${preset} caption + ${n} (${faceName}, ${order}): no overlap`, () => {
          const env = makeEnv(preset, { face, images: IMAGES });
          const card = itemOf(n, SAMPLES[n], 0, 3);
          const cap = captionItem(0, 3);
          const ctx = fakeCtx();
          const run = { card: () => scene(n).layout(ctx, card, env), caption: () => scene("caption").layout(ctx, cap, env) };
          const seq = order === "card first" ? ["card", "caption"] : ["caption", "card"];
          const Ls = {};
          for (const k of seq) Ls[k] = run[k]();
          assert.ok(Ls.card.fits && Ls.caption.fits, "both fit");
          const a = drawAll(scene(n), card, env, ctx);
          const b = drawAll(scene("caption"), cap, env, ctx);
          // 9:16 keeps its placement as before: with a face in the middle there is not
          // always room for both, and the renderer then reports the overlap (overlapsBlock).
          const crowded =
            (preset === "9:16" && (Ls.card.overlapsBlock || Ls.caption.overlapsBlock)) ||
            (face && (preset === "1:1" || preset === "4:5") && (Ls.card.overlapsFace || Ls.card.overlapsBlock || Ls.caption.overlapsBlock));
          if (!crowded) {
            // The laid-out blocks (transitions may grow the drawn boxes by their bleed
            // while they are still fading in).
            assert.equal(overlapArea(Ls.card.box, Ls.caption.box), 0, `card ${Ls.card.box} caption ${Ls.caption.box}`);
          }
          if (face && !crowded) assert.equal(overlapArea(b, face), 0);
        });
      }
  }
}

// --- landscape rules ---------------------------------------------------------------

test("16:9 cards sit beside the picture or at the foot, never over the middle", () => {
  const { size } = PRESETS["16:9"];
  const [W, H] = size;
  const middle = [W * 0.4, H * 0.3, W * 0.6, H * 0.7];
  for (const n of CARDS.filter((n) => !["screenshot_frame", "lower_third"].includes(n))) {
    const env = makeEnv("16:9", { face: faceBox(size) });
    const L = scene(n).layout(fakeCtx(), itemOf(n), env);
    assert.equal(overlapArea(L.box, middle), 0, `${n} ${L.box}`);
    assert.ok(L.box[2] - L.box[0] <= W * 0.6, `${n} is not wider than 60 % of the frame`);
  }
  // Single-column cards take the right side first.
  for (const n of ["number_card", "list_item", "chat_bubble", "bar_chart"]) {
    const L = scene(n).layout(fakeCtx(), itemOf(n), makeEnv("16:9"));
    assert.ok(L.box[0] > W / 2, `${n} on the right: ${L.box}`);
  }
});

test("16:9 lower third: bottom left, above the caption, which stays at the bottom centre", () => {
  const env = makeEnv("16:9");
  const ctx = fakeCtx();
  const Llt = scene("lower_third").layout(ctx, itemOf("lower_third"), env);
  const [ax0] = allowedArea(PRESETS["16:9"].size, PRESETS["16:9"].safe);
  assert.ok(Llt.box[0] < 1920 / 4 && Llt.box[0] >= ax0, `left: ${Llt.box}`);
  assert.ok(Llt.box[1] > 1080 / 3, `lower part: ${Llt.box}`);
  const Lc = scene("caption").layout(ctx, captionItem(), env);
  assert.equal(overlapArea(Llt.box, Lc.box), 0);
  assert.ok(Llt.box[3] < Lc.box[1], "lower third above the caption");
  const alone = scene("caption").layout(fakeCtx(), captionItem(), makeEnv("16:9"));
  assert.deepEqual(alone.box, Lc.box, "the caption does not move for the lower third");
});

test("16:9 keyword stands in a side column, two or three lines, not over the middle", () => {
  const L = scene("keyword").layout(fakeCtx(), itemOf("keyword"), makeEnv("16:9"));
  assert.ok(L.box[0] > 1920 / 2, `right side: ${L.box}`);
  assert.ok(L.box[2] - L.box[0] <= 1920 * 0.36 + 1);
  assert.ok(L.lines.length >= 2 && L.lines.length <= 3);
});

test("16:9 type scale follows the short side: card text is as large as in 9:16", () => {
  for (const n of ["number_card", "list_item", "chat_bubble", "lower_third"]) {
    const a = scene(n).layout(fakeCtx(), itemOf(n), makeEnv("9:16"));
    const b = scene(n).layout(fakeCtx(), itemOf(n), makeEnv("16:9"));
    assert.ok(b.px <= a.px * 1.001 && b.px >= a.px * 0.6 - 1, `${n}: ${b.px} vs ${a.px}`);
  }
});

test("16:9 screenshots keep their aspect: a landscape one fills, a portrait one leaves side space", () => {
  const mod = scene("screenshot_frame");
  const [W, H] = PRESETS["16:9"].size;
  for (const [file, shot] of [["shot.png", SHOTS.landscape], ["phone.png", SHOTS.portrait]]) {
    const env = makeEnv("16:9", { face: faceBox([W, H]), images: IMAGES });
    const item = itemOf("screenshot_frame", { file }, 0, 3);
    const L = mod.layout(fakeCtx(), item, env);
    assert.ok(L.fits && L.stage);
    assert.ok(Math.abs(L.iw / L.ih - shot.width / shot.height) < 0.01, `${file} aspect ${L.iw / L.ih}`);
    const usable = H - 65 - 130;
    if (file === "shot.png") {
      assert.ok(L.ih >= usable * 0.5, `fills the area above the caption strip: ${L.ih} of ${usable}`);
      assert.ok(Math.abs((L.box[0] + L.box[2]) / 2 - W / 2) < 1, "centred");
    } else {
      assert.ok(L.iw < W * 0.3, `portrait leaves side space: ${L.iw}`);
      assert.ok(L.box[0] > W / 2, "at the right edge");
      assert.ok(L.ih >= usable * 0.5);
    }
    // The caption below stays clear of it.
    const C = scene("caption").layout(fakeCtx(), captionItem(0, 3), env);
    assert.equal(overlapArea(L.box, C.box), 0);
  }
});

test("1:1 and 4:5: the caption is at the bottom, a card above its strip, centred without a face", () => {
  for (const preset of ["1:1", "4:5"]) {
    const { size, safe } = PRESETS[preset];
    const env = makeEnv(preset);
    const L = scene("number_card").layout(fakeCtx(), itemOf("number_card"), env);
    assert.ok(L.fits && !L.overlapsFace, preset);
    assert.ok(Math.abs((L.box[0] + L.box[2]) / 2 - size[0] / 2) < 1, `${preset} centred`);
    const C = scene("caption").layout(fakeCtx(), captionItem(), env);
    assert.ok(C.box[3] >= size[1] - safe.bottom - 2 * C.reach, `${preset} caption at the bottom: ${C.box}`);
    assert.ok(L.box[3] < C.box[1], `${preset} card above the caption`);
  }
});

test("a call without width/height still lays out as 1080x1920", () => {
  const a = makeEnv("9:16");
  delete a.width;
  delete a.height;
  const b = makeEnv("9:16");
  for (const n of CARDS.filter((n) => n !== "screenshot_frame")) {
    const La = scene(n).layout(fakeCtx(), itemOf(n), a);
    const Lb = scene(n).layout(fakeCtx(), itemOf(n), b);
    assert.deepEqual(La.box, Lb.box, n);
  }
});

// --- the real renderer ---------------------------------------------------------------

const TOOLS = path.join(__dirname, "..", "..", ".tools");
const haveBrowser = fs.existsSync(path.join(TOOLS, "ms-playwright"));

for (const preset of Object.keys(PRESETS))
  test(`render.js --boxes at ${preset}: every box inside the frame, no cramped item`, { skip: !haveBrowser && "no browser", timeout: 120000 }, () => {
    const { size, safe } = PRESETS[preset];
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "vc-formats-"));
    try {
      const overlays = [
        { scene: "number_card", t0: 0, t1: 1, props: SAMPLES.number_card },
        { scene: "lower_third", t0: 1, t1: 2, props: SAMPLES.lower_third },
        { scene: "chat_bubble", t0: 2, t1: 3, props: SAMPLES.chat_bubble },
        { scene: "split_compare", t0: 3, t1: 4, props: SAMPLES.split_compare },
        { scene: "bar_chart", t0: 4, t1: 5, props: SAMPLES.bar_chart },
        { scene: "list_item", t0: 5, t1: 6, props: SAMPLES.list_item },
        { scene: "keyword", t0: 6, t1: 7, props: SAMPLES.keyword },
        { scene: "cta_end", t0: 7, t1: 8, props: { text: SAMPLES.cta_end.text } },
      ];
      const captions = [{ t0: 0, t1: 7, words: captionItem(0, 7).words }];
      // Width/height instead of size: both spellings work.
      const spec = { width: size[0], height: size[1], fps: 10, duration: 8, safe, tokens: tokens(), overlays, captions };
      const input = path.join(dir, "overlays.json");
      fs.writeFileSync(input, JSON.stringify(spec));
      const out = spawnSync("node", [path.join(__dirname, "..", "render.js"), input, "--sheet", path.join(dir, "sheet.jpg"), "--boxes", path.join(dir, "boxes.json")], {
        encoding: "utf8",
        env: { ...process.env, VIDEOCUT_JOB_DIR: dir },
      });
      assert.equal(out.status, 0, out.stderr || out.stdout);
      const summary = JSON.parse(out.stdout.trim().split("\n").pop());
      assert.deepEqual(summary.size, size);
      assert.equal(summary.overlaps.safe_zone, 0);
      assert.equal(summary.overlaps.face, 0);
      const boxes = JSON.parse(fs.readFileSync(path.join(dir, "boxes.json"), "utf8"));
      const list = boxes;
      assert.ok(fs.statSync(path.join(dir, "sheet.jpg")).size > 1000);
      assert.ok(list.length >= 8);
      for (const b of list) assert.ok(b.box[0] >= 0 && b.box[1] >= 0 && b.box[2] <= size[0] && b.box[3] <= size[1], JSON.stringify(b));
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
