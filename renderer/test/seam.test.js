// Captions on the seam of a stacked slide_split view (9:16 and 4:5): centred on the
// boundary between the slide part and the speaker part, off the slide's content box and
// off the face from the brow line down.
const test = require("node:test");
const assert = require("node:assert/strict");
const { placeBlock, overlapArea, faceCore } = require("../lib/layout");
const caption = require("../scenes/caption");
const { PRESETS, makeEnv, fakeCtx, captionItem } = require("./formats_env");

// The core's geometry (tools/_layouts.slide_geometry, SPLIT_TOP 0.58, SLIDE_SEAM 0.12 / 0.09):
// the slide part on top, the speaker part below, the content box SLIDE_SEAM of the short
// side above the seam.
function split(preset) {
  const { size, safe } = PRESETS[preset];
  const [w, h] = size;
  const seam = Math.round(h * 0.58);
  const margin = 2 * Math.round((Math.min(w, h) * (h / w >= 1.6 ? 0.12 : 0.09)) / 2);
  return { seam, part: [0, 0, w, seam], content: [safe.side, safe.top, w - safe.side, seam - margin] };
}

// A selfie face in the speaker part: head near its top, the chin above the bottom safe zone.
const FACES = { "9:16": [380, 1290, 700, 1640], "4:5": [400, 860, 680, 1120] };

// The page's env (render.js pageMain) for one slide_split view over [0, 10] s.
function splitEnv(preset, { seam = true, face = FACES[preset] } = {}) {
  const env = makeEnv(preset, { face });
  const s = split(preset);
  const view = { t0: 0, t1: 10, box: s.part, hard: true };
  const blocks = [view];
  env.blocksDuring = (t0, t1) => blocks.filter((b) => b.t0 < t1 && b.t1 > t0).map((b) => b.box);
  env.addBlock = (t0, t1, box) => blocks.push({ t0, t1, box });
  env.hardDuring = () => [s.part];
  if (seam) env.seamDuring = () => ({ y: s.seam, content: [s.content], parts: [s.part] });
  return { env, ...s };
}

for (const preset of ["9:16", "4:5"]) {
  test(`${preset} slide_split: the caption sits on the seam, off the content box and the eyes`, () => {
    const { env, seam, content } = splitEnv(preset);
    const L = caption.layout(fakeCtx(), captionItem(1, 3), env);
    assert.equal(L.band, "seam");
    assert.ok(L.box[1] < seam && L.box[3] > seam, `straddles the seam ${seam}: ${L.box}`);
    const m = L.reach;
    assert.equal(overlapArea(L.box, [content[0] - m, content[1] - m, content[2] + m, content[3] + m]), 0, "off the content box");
    assert.equal(overlapArea(L.box, faceCore(FACES[preset])), 0, "off the face from the brow line down");
    assert.equal(L.overlapsFace, false);
    // Close to centred: at least a third of the block lies above the seam.
    assert.ok(seam - L.box[1] >= (L.box[3] - L.box[1]) / 3, `${L.box}`);
  });

  test(`${preset} slide_split without the seam: the caption ends up on the face (the old rule)`, () => {
    const { env, seam } = splitEnv(preset, { seam: false });
    const L = caption.layout(fakeCtx(), captionItem(1, 3), env);
    assert.ok(L.box[1] >= seam, `${L.box}`);
    assert.ok(L.overlapsFace, `${L.box}`);
  });
}

test("seam: a caption outside the slide_split view keeps its own band", () => {
  const { env } = splitEnv("9:16");
  env.seamDuring = () => null;
  env.hardDuring = () => [];
  env.blocksDuring = () => [];
  const L = caption.layout(fakeCtx(), captionItem(20, 22), env);
  assert.notEqual(L.band, "seam");
});

test("placeBlock seam: centred on the seam, pushed below a content box that reaches lower", () => {
  const size = [1080, 1920];
  const safe = { top: 250, bottom: 350, side: 65 };
  const r = placeBlock({ size, safe, w: 600, h: 200, position: "seam", seam: 1114, hard: [[65, 250, 1015, 984]], margin: 30 });
  assert.equal(r.band, "seam");
  assert.deepEqual([r.box[1], r.box[3]], [1014, 1214]);
  const low = placeBlock({ size, safe, w: 600, h: 200, position: "seam", seam: 1114, hard: [[65, 250, 1015, 1050]], margin: 30 });
  assert.equal(low.band, "seam");
  assert.equal(low.box[1], 1080, "just below the content box grown by the margin");
});

test("placeBlock seam: hair and forehead may be covered, the eyes not", () => {
  const size = [1080, 1920];
  const safe = { top: 250, bottom: 350, side: 65 };
  const base = { size, safe, w: 600, h: 200, position: "seam", seam: 1114, hard: [[65, 250, 1015, 984]], margin: 30 };
  // Face top 1180: the block (to 1214) covers its top strip, the brow line is at 1255.
  const forehead = placeBlock({ ...base, face: [380, 1180, 700, 1480] });
  assert.equal(forehead.band, "seam");
  assert.equal(forehead.overlapsFace, false);
  // A face reaching up to the seam: its eyes would be under the block, it moves below.
  const eyes = placeBlock({ ...base, face: [380, 1060, 700, 1300] });
  assert.equal(overlapArea(eyes.box, faceCore([380, 1060, 700, 1300])), 0, `${eyes.box}`);
  assert.equal(eyes.band, "lower");
  // The first face without the seam rule counts from its top: no spot keeps it clear.
  const plain = placeBlock({ ...base, position: "lower", seam: null, face: [380, 1180, 700, 1480] });
  assert.equal(plain.overlapsFace, true);
});

test("splitAtSeams: a caption group across the start or end of a seam view is cut there", () => {
  const { splitAtSeams } = require("../lib/seams");
  const views = [{ t0: 3, t1: 7, screen: [0, 0, 1080, 1114], seam: 1114 }, { t0: 9, t1: 12, screen: [0, 0, 1, 1] }];
  const w = (x, t0) => ({ w: x, t0, t1: t0 + 0.2 });
  const caps = [
    { t0: 2, t1: 4, words: [w("a", 1.98), w("b", 2.5), w("c", 3.1), w("d", 3.6)] }, // across the start
    { t0: 4, t1: 5, words: [w("e", 4)] }, // inside: unchanged
    { t0: 6.5, t1: 7.5, words: [w("f", 6.6), w("g", 6.8)] }, // across the end, no word after it
    { t0: 8.5, t1: 9.5, words: [w("h", 8.6), w("i", 9.2)] }, // a view without a seam: unchanged
    { t0: 6.8, t1: 8, words: [w("j", 6.85), w("k", 7.3), w("l", 7.6)] }, // 0.2 s before the end
    { t0: 1, t1: 3.0004, words: [{ w: "m" }, w("n", 2)] }, // ends at the start (rounding)
  ];
  const out = splitAtSeams(caps, views);
  assert.deepEqual(
    out.map((c) => [c.t0, c.t1, c.words.map((x) => x.w).join("")]),
    [[2, 3, "ab"], [3, 4, "cd"], [4, 5, "e"], [6.5, 7, "fg"], [8.5, 9.5, "hi"], [7, 8, "jkl"], [1, 3.0004, "mn"]],
  );
  assert.equal(out[2], caps[1]);
});
