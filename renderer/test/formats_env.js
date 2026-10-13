// Shared fixtures of the output-format tests: the four presets with their safe zones,
// a canvas stand-in that records the text it draws, and a scene environment.
const fs = require("fs");
const path = require("path");
const { faceAt, faceDuring } = require("../lib/layout");
const { step } = require("../lib/spring");

// Presets of the output-format contract. Safe zones are fixtures in the shape the core
// gives the renderer ({top, bottom, side} in px): 9:16 as today, landscape top ~6 %,
// bottom ~12 %, sides ~4 %; the square and 4:5 values are in between.
const PRESETS = {
  "9:16": { size: [1080, 1920], safe: { top: 250, bottom: 350, side: 65 } },
  "16:9": { size: [1920, 1080], safe: { top: 65, bottom: 130, side: 77 } },
  "1:1": { size: [1080, 1080], safe: { top: 108, bottom: 162, side: 65 } },
  "4:5": { size: [1080, 1350], safe: { top: 160, bottom: 240, side: 65 } },
};

const BRAND = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "brands", "_example", "brand.json"), "utf8"));

function tokens(transitions = "blur", spring = { w: 13, z: 0.9 }) {
  const t = JSON.parse(JSON.stringify(BRAND));
  t.motion = { ...t.motion, transitions, spring };
  const c = t.colors;
  t.caption_colors = { text: c.text, box: c.surface, highlight: c.accent };
  return t;
}

// Canvas stand-in: every glyph is 0.55 em wide. `texts` records what fillText drew as
// {text, x0, x1, y, size} (extent after textAlign); other calls are logged.
function fakeCtx(log = [], texts = []) {
  const state = { font: "10px x", globalAlpha: 1, textAlign: "left" };
  const px = () => Number(/(\d+(?:\.\d+)?)px/.exec(state.font)[1]);
  const own = {
    log,
    texts,
    measureText: (s) => ({ width: String(s).length * px() * 0.55 }),
    fillText: (s, x, y) => {
      const w = String(s).length * px() * 0.55;
      const x0 = state.textAlign === "center" ? x - w / 2 : state.textAlign === "right" ? x - w : x;
      texts.push({ text: String(s), x0, x1: x0 + w, y, size: px() });
    },
  };
  return new Proxy(own, {
    get: (o, k) => (k in o ? o[k] : k in state ? state[k] : (...args) => log.push([k, ...args])),
    set: (o, k, v) => ((state[k] = v), true),
  });
}

function fakeCanvas(w, h) {
  const ctx = fakeCtx();
  return { width: w, height: h, ctx, getContext: () => ctx };
}

// A face-sized box, horizontally centred: 24 % of the short side wide in a landscape frame
// (centre height 0.42 h), 20 % in the others (a reframed head sits higher: 0.3 h).
function faceBox([w, h]) {
  const land = w / h > 1.2;
  const fw = Math.min(w, h) * (land ? 0.24 : 0.2);
  const cy = (land ? 0.42 : 0.3) * h;
  return [w / 2 - fw / 2, cy - fw * 0.55, w / 2 + fw / 2, cy + fw * 0.55];
}

// The speaker inset of a landscape screen layout: a bubble ~26 % of the frame height in
// the bottom-right corner, above the caption zone (the foot of the allowed area).
function pipBox([w, h], safe) {
  const d = Math.round(h * 0.26);
  const bottom = h - safe.bottom - Math.round(h * 0.2);
  return [w - safe.side - d, bottom - d, w - safe.side, bottom];
}

function makeEnv(preset, { face, images = {}, tok = tokens() } = {}) {
  const { size, safe } = PRESETS[preset];
  const samples = face ? [{ t: 0, box: face }, { t: 600, box: face }] : [];
  const memo = new Map();
  const blocks = [];
  return {
    tokens: tok,
    size,
    width: size[0],
    height: size[1],
    safe,
    fonts: { family: "x", ready: true },
    faceAt: (t) => faceAt(samples, t),
    faceDuring: (t0, t1) => faceDuring(samples, t0, t1),
    spring: step,
    once: (item, fn) => {
      if (!memo.has(item)) memo.set(item, fn());
      return memo.get(item);
    },
    blocksDuring: (t0, t1) => blocks.filter((b) => b.t0 < t1 && b.t1 > t0).map((b) => b.box),
    addBlock: (t0, t1, box) => blocks.push({ t0, t1, box }),
    image: (ref) => images[ref] || null,
    canvas: fakeCanvas,
  };
}

const SAMPLES = {
  number_card: { value: "1,250 %", label: "of all requests answered on the same day" },
  list_item: { index: 2, text: "Write the hook before anything else", total: 3 },
  chat_bubble: { text: "How long does a first appointment usually take?", from: "viewer" },
  screenshot_frame: { file: "shot.png", focus: [0.6, 0.1, 0.9, 0.3], redact: [[0.1, 0.1, 0.4, 0.2]] },
  split_compare: { left: { title: "Myth", text: "You need expensive gear to start" }, right: { title: "Reality", text: "A phone and a window are enough" } },
  bar_chart: { bars: [{ label: "Before", value: 12 }, { label: "After", value: 31 }, { label: "Goal", value: 40 }], unit: "%" },
  lower_third: { title: "Topic of the day", subtitle: "Part 2 of 5" },
  keyword: { text: "Consistency wins" },
  cta_end: { text: "Send this to someone who needs it", logo: true },
};

const CAPTION_WORDS = "die mir beim Laufen am Meer wirklich".split(" ");
const captionItem = (t0 = 0, t1 = 3) => ({
  scene: "caption",
  t0,
  t1,
  words: CAPTION_WORDS.map((w, i) => ({ w, t0: t0 + i * 0.2, t1: t0 + i * 0.2 + 0.2 })),
});

module.exports = { pipBox, PRESETS, BRAND, tokens, fakeCtx, makeEnv, faceBox, SAMPLES, captionItem };
