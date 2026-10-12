const test = require("node:test");
const assert = require("node:assert/strict");
const { breakLines, fitText, faceAt, faceDuring, placeBlock, overlapArea, allowedArea } = require("../lib/layout");
const { union, BoxCollector } = require("../lib/boxes");

// Deterministic stand-in for a canvas context: every glyph is 0.55 em wide.
function fakeCtx() {
  return {
    font: "",
    save() {},
    restore() {},
    measureText(s) {
      const px = Number(/(\d+(?:\.\d+)?)px/.exec(this.font)[1]);
      return { width: s.length * px * 0.55 };
    },
  };
}

const SIZE = [1080, 1920];
const SAFE = { top: 250, bottom: 350, side: 65 };
const inside = (b, a) => b[0] >= a[0] && b[1] >= a[1] && b[2] <= a[2] && b[3] <= a[3];

// Small deterministic PRNG for synthetic cases (tests only; the renderer has none).
function lcg(seed) {
  let s = seed >>> 0;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32);
}

test("breakLines is greedy and keeps every word", () => {
  const lines = breakLines([100, 100, 100, 100], 10, 220);
  assert.deepEqual(lines.map((l) => [l.from, l.to, l.width]), [[0, 2, 210], [2, 4, 210]]);
  assert.deepEqual(breakLines([500, 50], 10, 200).map((l) => [l.from, l.to]), [[0, 1], [1, 2]]);
  assert.deepEqual(breakLines([], 10, 200), []);
});

test("fitText shrinks until the words fit the width and line limit", () => {
  const ctx = fakeCtx();
  const texts = "a rather long caption group with several words".split(" ");
  const fit = fitText(ctx, texts, { font: (p) => `800 ${p}px x`, px: 120, maxWidth: 900, maxLines: 2 });
  assert.ok(fit.px < 120);
  assert.ok(fit.lines.length <= 2);
  assert.ok(fit.width <= 900);
  assert.equal(fit.height, fit.lines.length * fit.lineHeight);
});

test("faceAt interpolates and holds the edges; faceDuring covers the interval", () => {
  const s = [{ t: 0, box: [0, 0, 10, 10] }, { t: 1, box: [10, 10, 20, 20] }, { t: 2, box: [100, 100, 110, 110] }];
  assert.deepEqual(faceAt(s, 0.5), [5, 5, 15, 15]);
  assert.deepEqual(faceAt(s, -1), [0, 0, 10, 10]);
  assert.deepEqual(faceAt(s, 9), [100, 100, 110, 110]);
  assert.equal(faceAt([], 1), null);
  assert.deepEqual(faceDuring(s, 0.2, 0.8), [0, 0, 20, 20]);
  assert.deepEqual(faceDuring(s, 0.5, 1.5), [0, 0, 110, 110]);
});

test("without a face the block sits centred in the allowed band", () => {
  const { box } = placeBlock({ size: SIZE, safe: SAFE, w: 600, h: 200 });
  const a = allowedArea(SIZE, SAFE);
  assert.ok(inside(box, a));
  assert.equal(box[0] + box[2], SIZE[0]);
  assert.ok(Math.abs((box[1] + box[3]) / 2 - (a[1] + a[3]) / 2) <= 1);
});

test("never overlaps the face box or the safe zones on synthetic inputs", () => {
  const rnd = lcg(7);
  let moved = 0;
  for (let n = 0; n < 2000; n++) {
    const fw = 200 + rnd() * 400;
    const fh = 250 + rnd() * 350;
    const fx = 65 + rnd() * (SIZE[0] - 130 - fw);
    const fy = 250 + rnd() * (SIZE[1] - 600 - fh);
    const face = [fx, fy, fx + fw, fy + fh];
    const w = 300 + rnd() * 500;
    const h = 90 + rnd() * 200;
    const inset = Math.round(rnd() * 40);
    const r = placeBlock({ size: SIZE, safe: SAFE, w, h, face, margin: 24, inset, position: rnd() < 0.5 ? "center" : "lower" });
    // A free spot exists above or below the face when the band has room for it.
    const a = allowedArea(SIZE, SAFE, inset);
    const room = Math.max(face[1] - 24 - a[1], a[3] - (face[3] + 24));
    if (room >= h + 1) {
      assert.equal(r.overlapsFace, false, `case ${n}`);
      assert.equal(overlapArea(r.box, face), 0, `case ${n}`);
    }
    assert.ok(inside(r.box, a), `case ${n}: ${r.box} outside ${a}`);
    const centred = placeBlock({ size: SIZE, safe: SAFE, w, h, inset });
    if (centred.box[1] !== r.box[1]) moved++;
  }
  assert.ok(moved > 100, "the face actually pushed blocks around");
});

test("a block avoids other blocks when there is room, and the face first", () => {
  const face = [340, 300, 740, 700];
  const first = placeBlock({ size: SIZE, safe: SAFE, w: 600, h: 200, face });
  const second = placeBlock({ size: SIZE, safe: SAFE, w: 600, h: 200, face, avoid: [first.box] });
  assert.equal(overlapArea(second.box, first.box), 0);
  assert.equal(overlapArea(second.box, face), 0);
  assert.equal(second.overlapsBlock, false);
});

test("close-up: a caption sits in the lower band (below the face), not above the head", () => {
  // Synthetic close-ups: the face box covers the centre band, the chest area below it has room.
  const rnd = lcg(11);
  for (let n = 0; n < 500; n++) {
    const fw = 500 + rnd() * 300;
    const fx = (SIZE[0] - fw) / 2;
    const fy = 300 + rnd() * 250;
    const fb = 1100 + rnd() * 200;
    const face = [fx, fy, fx + fw, fb];
    const h = 100 + rnd() * 120;
    for (const position of ["center", "lower"]) {
      const r = placeBlock({ size: SIZE, safe: SAFE, w: 700, h, face, margin: 24, inset: 12, position });
      const a = allowedArea(SIZE, SAFE, 12);
      if (a[3] - (fb + 24) < h) continue; // no room below the face in this case
      assert.equal(r.band, "lower", `case ${n} ${position}`);
      assert.ok(r.box[1] >= fb, `case ${n} ${position}: ${r.box} not below the face ${face}`);
      assert.ok(inside(r.box, a), `case ${n} ${position}: ${r.box} in a safe zone`);
      assert.equal(r.overlapsFace, false);
    }
  }
});

test("close-up without room below the face falls back above the head", () => {
  const face = [240, 700, 840, 1500];
  const r = placeBlock({ size: SIZE, safe: SAFE, w: 700, h: 160, face, position: "lower" });
  assert.equal(r.overlapsFace, false);
  assert.ok(r.box[3] <= face[1]);
  assert.ok(inside(r.box, allowedArea(SIZE, SAFE)));
});

test("position is a preference: centre when the face leaves it free, lower when asked", () => {
  const face = [390, 280, 690, 620]; // a small face high in the frame
  const c = placeBlock({ size: SIZE, safe: SAFE, w: 600, h: 160, face, position: "center" });
  const l = placeBlock({ size: SIZE, safe: SAFE, w: 600, h: 160, face, position: "lower" });
  assert.equal(c.band, "center");
  assert.equal(l.band, "lower");
  assert.ok(l.box[1] > c.box[1]);
});

test("boxes: union and collection clip to the frame", () => {
  assert.deepEqual(union(null, [1, 2, 3, 4]), [1, 2, 3, 4]);
  assert.deepEqual(union([0, 5, 2, 6], [1, 2, 3, 4]), [0, 2, 3, 6]);
  const c = new BoxCollector([100, 100]);
  c.add(0, { scene: "keyword", t0: 0, t1: 1 }, [10.4, 10.6, 20.2, 20.8]);
  c.add(0, { scene: "keyword", t0: 0, t1: 1 }, [-5, 15, 30, 120]);
  c.add(1, { scene: "caption", t0: 1, t1: 2 }, null);
  assert.deepEqual(c.list(), [{ scene: "keyword", t0: 0, t1: 1, box: [0, 10, 30, 100] }]);
  // With output times: the first and last moment the item drew.
  const d = new BoxCollector([100, 100]);
  d.add(0, { scene: "caption", t0: 1, t1: 3 }, [1, 1, 2, 2], 1.5);
  d.add(0, { scene: "caption", t0: 1, t1: 3 }, [1, 1, 2, 2], 1.2);
  d.add(0, { scene: "caption", t0: 1, t1: 3 }, [1, 1, 2, 2], 2.0004);
  assert.deepEqual(d.list()[0].drawn, [1.2, 2]);
});

test("plates: captions are hidden while a full-frame plate is on screen", () => {
  const { platesOf, hidesCaptions } = require("../lib/plates");
  const scenes = { cta_end: require("../scenes/cta_end"), keyword: require("../scenes/keyword") };
  const items = [
    { scene: "keyword", t0: 1, t1: 3 },
    { scene: "cta_end", t0: 10, t1: 12.5 },
  ];
  const plates = platesOf(items, scenes);
  assert.deepEqual(plates.map((p) => p.scene), ["cta_end"]);
  assert.equal(hidesCaptions(plates, 2), false);
  assert.equal(hidesCaptions(plates, 9.99), false);
  assert.equal(hidesCaptions(plates, 10), true);
  assert.equal(hidesCaptions(plates, 12.5), true);
});
