// Scenes take every colour, font and motion value from env.tokens. This lint keeps
// literal colours and font names out of renderer/scenes (and the scene helpers).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const DIRS = ["scenes"].map((d) => path.join(__dirname, "..", d));
const HELPERS = ["textblock.js", "style.js", "layout.js"].map((f) => path.join(__dirname, "..", "lib", f));
const files = [...DIRS.flatMap((d) => fs.readdirSync(d).filter((f) => f.endsWith(".js")).map((f) => path.join(d, f))), ...HELPERS];

const strip = (src) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
const strings = (src) => [...src.matchAll(/(["'`])((?:\\.|(?!\1).)*)\1/g)].map((m) => m[2]);

const COLOR_WORDS = /\b(black|white|red|green|blue|yellow|orange|purple|pink|gr[ae]y|transparent)\b/i;
const FONT_WORDS = /\b(sans-serif|serif|monospace|cursive|fantasy|system-ui|ui-sans-serif|Arial|Helvetica|Inter|Roboto|Georgia|Times)\b/i;

test("there are scenes to lint", () => {
  assert.ok(files.some((f) => f.includes(`${path.sep}scenes${path.sep}`)));
});

for (const file of files) {
  const rel = path.relative(path.join(__dirname, ".."), file);
  test(`${rel}: no literal colours`, () => {
    const src = strip(fs.readFileSync(file, "utf8"));
    assert.equal((src.match(/#[0-9a-f]{3,8}\b/gi) || []).length, 0, "hex colour");
    assert.equal((src.match(/\b(rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\s*\(/gi) || []).length, 0, "colour function");
    for (const s of strings(src)) assert.ok(!COLOR_WORDS.test(s), `colour name in "${s}"`);
  });
  test(`${rel}: no font names`, () => {
    const src = strip(fs.readFileSync(file, "utf8"));
    for (const s of strings(src)) {
      assert.ok(!FONT_WORDS.test(s), `font name in "${s}"`);
      // A quoted capitalised name like "Some Font" is a font family, not code.
      assert.ok(!/^[A-Z][a-z]+(?: [A-Z][a-z]+)*$/.test(s), `font-like name "${s}"`);
    }
  });
}

test("every scene exports name and draw(ctx, t, item, env)", () => {
  for (const f of fs.readdirSync(DIRS[0]).filter((f) => f.endsWith(".js"))) {
    const m = require(path.join(DIRS[0], f));
    assert.equal(m.name, f.slice(0, -3));
    assert.equal(typeof m.draw, "function");
    assert.equal(m.draw.length, 4);
  }
});
