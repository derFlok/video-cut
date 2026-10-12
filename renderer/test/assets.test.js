// SVG logos without width/height decode to 0x0 in an <img>; the asset reader sizes them.
const test = require("node:test");
const assert = require("node:assert/strict");
const { sizedSvg } = require("../lib/assets");

const size = (svg) => {
  const tag = /<svg\b[^>]*>/.exec(svg)[0];
  return [/\swidth="([^"]*)"/.exec(tag)[1], /\sheight="([^"]*)"/.exec(tag)[1]];
};

test("an SVG with only a viewBox gets a size with its aspect", () => {
  const svg = '<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 50"><path d="M0 0h10"/></svg>';
  const out = sizedSvg(svg);
  assert.deepEqual(size(out), ["1024", "256"]);
  assert.ok(out.endsWith('<path d="M0 0h10"/></svg>'));
});

test("percent sizes are replaced, absolute sizes kept", () => {
  assert.deepEqual(size(sizedSvg('<svg width="100%" height="100%" viewBox="0,0,10,20"/>')), ["512", "1024"]);
  const fixed = '<svg width="120" height="40" viewBox="0 0 3 1"/>';
  assert.equal(sizedSvg(fixed), fixed);
  assert.deepEqual(size(sizedSvg("<svg/>")), ["1024", "1024"]);
});
