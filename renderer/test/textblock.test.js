// A word scaled by the "scale" highlight pushes its line apart instead of running into
// its neighbours (upper case + scale used to swallow the space between two words).
const test = require("node:test");
const assert = require("node:assert/strict");
const { reflow, SCALE_GROWTH } = require("../lib/textblock");

test("a scaled word keeps the gaps to its neighbours", () => {
  const space = 18;
  const widths = [220, 310, 180];
  let x = 0;
  const words = widths.map((width) => {
    const w = { x, y: 100, width };
    x += width + space;
    return w;
  });
  words.push({ x: 0, y: 160, width: 200 }); // another line: never moved
  const states = [{ scale: 0 }, { scale: 1 }, { scale: 0 }, { scale: 0 }];
  const { dx, spread } = reflow(words, states);
  const g = (SCALE_GROWTH * widths[1]) / 2;
  assert.deepEqual(dx, [-g, 0, g, 0]);
  assert.equal(spread, g);
  // drawn extents: the scaled word grows by g on each side around its centre
  const left = [words[0].x + dx[0], words[0].x + dx[0] + widths[0]];
  const mid = [words[1].x - g, words[1].x + widths[1] + g];
  const right = [words[2].x + dx[2], words[2].x + dx[2] + widths[2]];
  assert.ok(mid[0] - left[1] >= space - 1e-9 && right[0] - mid[1] >= space - 1e-9);
});

test("no scale, no shift", () => {
  const words = [{ x: 0, y: 0, width: 100 }, { x: 120, y: 0, width: 80 }];
  assert.deepEqual(reflow(words, [{ scale: 0 }, { scale: 0 }]), { dx: [0, 0], spread: 0 });
});
