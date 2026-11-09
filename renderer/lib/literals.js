// Brand-tokens-only check for agent-written scene modules (the same rules the lint test
// applies to renderer/scenes, renderer/slides and renderer/motion): no literal colours,
// no font names. Returns a list of problems (empty when clean).
const COLOR_WORDS = /\b(black|white|red|green|blue|yellow|orange|purple|pink|gr[ae]y|transparent)\b/i;
const FONT_WORDS = /\b(sans-serif|serif|monospace|cursive|fantasy|system-ui|ui-sans-serif|Arial|Helvetica|Inter|Roboto|Georgia|Times)\b/i;

const strip = (src) => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
const strings = (src) => [...src.matchAll(/(["'`])((?:\\.|(?!\1).)*)\1/g)].map((m) => m[2]);

function literalErrors(src) {
  const s = strip(src);
  const errs = [];
  if (/#[0-9a-f]{3,8}\b/i.test(s)) errs.push("hex colour");
  if (/\b(rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\s*\(/i.test(s)) errs.push("colour function");
  for (const t of strings(s)) {
    if (COLOR_WORDS.test(t)) errs.push(`colour name in "${t.slice(0, 40)}"`);
    if (FONT_WORDS.test(t)) errs.push(`font name in "${t.slice(0, 40)}"`);
    if (/font-family\s*:/i.test(t) && !/var\(--vc-font\)/.test(t)) errs.push("font-family other than var(--vc-font)");
  }
  return errs;
}

module.exports = { literalErrors, COLOR_WORDS, FONT_WORDS };
