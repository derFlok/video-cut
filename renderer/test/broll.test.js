// Captions over b-roll (overlays.json views with layout broll, band "lower"): the material
// fills the frame and there is no face, so a 9:16 caption goes to the lower band.
const test = require("node:test");
const assert = require("node:assert/strict");
const caption = require("../scenes/caption");
const { makeEnv, fakeCtx, captionItem } = require("./formats_env");

test("9:16: a caption during a b-roll view sits in the lower band", () => {
  const env = makeEnv("9:16");
  env.bandDuring = (t0, t1) => (t0 < 5 && t1 > 2 ? "lower" : null);
  const L = caption.layout(fakeCtx(), captionItem(2, 4), env);
  assert.equal(L.band, "lower");
  const plain = caption.layout(fakeCtx(), captionItem(8, 10), makeEnv("9:16"));
  assert.equal(plain.band, "center", "outside b-roll the brand's band stays");
  assert.ok(L.box[1] > plain.box[1], `${L.box} below ${plain.box}`);
});
