// Slide templates (renderer/slides): every template lays out inside its content box in
// every output format at a scale of at least 0.6 with text near the limits of
// tools/slides.py, draws without error over its life, and builds its steps at the reveal
// times (and not before).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const S = require("../lib/slide");
const { PRESETS, tokens, fakeCtx } = require("./formats_env");

const DIR = path.join(__dirname, "..", "slides");
const NAMES = fs.readdirSync(DIR).filter((f) => f.endsWith(".js")).map((f) => f.slice(0, -3)).sort();
const tpl = (n) => require(path.join(DIR, `${n}.js`));
const FPS = 30;

// Content at (or near) the text limits of tools/slides.py.
const CONTENT = {
  title: { title: "So gewinnen kleine Teams jede Woche zwei Stunden zurück", subtitle: "Drei Gewohnheiten aus dem Alltag einer Werkstatt mit zwölf Leuten" },
  bullets: {
    title: "Was sich nach dem ersten Monat geändert hat",
    items: [
      "Jede Anfrage landet an einem Ort statt in fünf Postfächern",
      "Rückfragen werden am selben Tag beantwortet",
      "Termine bestätigen sich automatisch per Nachricht",
      "Niemand sucht mehr nach dem letzten Angebot",
      "Der Freitag gehört wieder der Planung",
    ],
  },
  big_number: { value: "12.000", label: "Anfragen im ersten Jahr, ohne zusätzliche Stelle im Büro", count: true },
  compare: {
    left: { title: "Vorher", items: ["Zettel, Anrufe und lose Notizen überall", "Angebote nach drei Tagen", "Keiner weiß, wer zuständig ist"] },
    right: { title: "Nachher", items: ["Ein Eingang für alle Anfragen", "Angebot noch am selben Tag", "Klare Zuständigkeit für jeden Fall"] },
  },
  steps: { title: "In vier Schritten zum ersten Termin", steps: ["Anfrage kommt über das Formular herein", "Zentrovia sortiert nach Dringlichkeit", "Das Team bestätigt mit einem Klick", "Kunde bekommt Termin und Erinnerung"] },
  timeline: {
    title: "Ein Jahr mit dem neuen Ablauf",
    points: [
      { label: "Januar", text: "Start mit zwei Leuten im Büro" },
      { label: "März", text: "Alle Anfragen laufen über einen Eingang" },
      { label: "Juni", text: "Erinnerungen senken Ausfälle deutlich" },
      { label: "September", text: "Zweiter Standort kommt dazu" },
      { label: "Dezember", text: "Zwölftausend Anfragen bearbeitet" },
    ],
  },
  bar_chart: { title: "Antwortzeit in Stunden", unit: "h", bars: [{ label: "Vorher", value: 26 }, { label: "Nach einem Monat", value: 9 }, { label: "Nach einem Quartal", value: 4.5 }, { label: "Heute", value: 2 }] },
  quote: { text: "Wir haben nichts Neues eingestellt, sondern nur aufgehört, dieselbe Information dreimal abzutippen. Das allein hat uns jede Woche einen halben Tag gebracht.", author: "Werkstattleitung, Betrieb mit zwölf Leuten" },
  cta: { title: "Teste es zwei Wochen lang kostenlos", subtitle: "Keine Kreditkarte, keine Einrichtung, einfach anfangen", url: "zentrovia.example/start" },
  image: { asset: "input/slides/page_01.png" },
};

// Content boxes like the core's (tools/_layouts.slide_geometry): the safe area (slide_full),
// above or beside the bubble (slide: two candidates, the renderer keeps the better), the
// slide part of a split.
function areas(preset) {
  const { size, safe } = PRESETS[preset];
  const [w, h] = size;
  const gap = 32;
  const full = [safe.side, safe.top, w - safe.side, h - safe.bottom];
  const land = w / h > 1.2;
  const portrait = h / w > 1.2;
  const split = land ? [safe.side, safe.top, Math.round(w * 0.64) - gap, h - safe.bottom] : [safe.side, safe.top, w - safe.side, Math.round(h * 0.58) - gap];
  const side = portrait ? Math.round(w * 0.34) : Math.round(h * 0.26);
  const by = h - safe.bottom - side;
  const bx = w - safe.side - side;
  const slide = [
    [safe.side, safe.top, w - safe.side, by - gap],
    [safe.side, safe.top, bx - gap, h - safe.bottom],
  ];
  return { slide_full: [full], slide, slide_split: [split] };
}

// The layout the page keeps: the candidate box where the text fits at the largest scale.
function layoutBest(n, slide, e, boxes) {
  let L = null;
  for (const area of boxes) {
    const c = tpl(n).layout(fakeCtx(), { ...slide, area }, e);
    const rank = (x) => (Number.isFinite(x.rank) ? x.rank : x.k);
    if (!L || (c.fits && !L.fits) || (c.fits === L.fits && rank(c) > rank(L))) L = c;
  }
  return L;
}

function env(preset, slide, extra = {}) {
  const { size } = PRESETS[preset];
  const e = {
    tokens: tokens(),
    size,
    width: size[0],
    height: size[1],
    unit: Math.min(...size),
    lang: "de",
    fps: FPS,
    duration: 8,
    fonts: { family: "x", ready: true },
    image: (ref) => (ref === "input/slides/page_01.png" ? { width: 1920, height: 1080 } : null),
    ...extra,
  };
  e.times = S.stepTimes(tpl(slide.template).steps(slide.content), slide.reveal);
  return e;
}

const inside = (b, a) => b[0] >= a[0] - 1 && b[1] >= a[1] - 1 && b[2] <= a[2] + 1 && b[3] <= a[3] + 1;

test("every template exports name, steps, layout and draw(ctx, t, slide, env, L)", () => {
  assert.deepEqual(NAMES, Object.keys(CONTENT).sort());
  for (const n of NAMES) {
    const m = tpl(n);
    assert.equal(m.name, n);
    assert.equal(typeof m.steps, "function");
    assert.equal(typeof m.layout, "function");
    assert.equal(m.draw.length, 5);
  }
});

for (const preset of Object.keys(PRESETS))
  for (const [layout, boxes] of Object.entries(areas(preset)))
    for (const n of NAMES) {
      test(`${preset} ${layout} ${n}: fits its box at >= 60 %, every text inside it, draws over its life`, () => {
        const slide = { template: n, content: CONTENT[n], area: boxes[0] };
        const e = env(preset, slide);
        const L = layoutBest(n, slide, e, boxes);
        const area = L.area;
        assert.ok(L.fits, `fits (scale ${L.k})`);
        assert.ok(L.k >= S.MIN_SCALE, `scale ${L.k}`);
        for (const t of L.texts) assert.ok(inside(t.box, area), `${t.text} ${t.box} outside ${area}`);
        if (n !== "image") assert.ok(L.texts.length > 0);
        const texts = [];
        for (let i = 0; i <= 8 * FPS; i += 7) tpl(n).draw(fakeCtx([], texts), i / FPS, slide, e, L);
        // the final state shows every text of the layout
        const last = [];
        tpl(n).draw(fakeCtx([], last), 7.9, slide, e, L);
        const drawn = last.map((x) => x.text).join(" ");
        for (const t of L.texts) for (const word of t.text.split(" ").slice(0, 2)) assert.ok(drawn.includes(word.replace(/[„“”]/g, "")), `${word} drawn`);
      });
    }

test("text over the limits does not fit even at 60 % (and says so)", () => {
  const slide = { template: "bullets", content: { title: "x ".repeat(30), items: Array(5).fill("lang ".repeat(30).trim()) }, area: [65, 250, 1015, 700] };
  const L = tpl("bullets").layout(fakeCtx(), slide, env("9:16", slide));
  assert.equal(L.fits, false);
  assert.equal(L.k, S.MIN_SCALE);
});

test("short content grows above the base size when there is room, never above 1.3", () => {
  const slide = { template: "title", content: { title: "Kurz" }, area: [65, 250, 1015, 1570] };
  const L = tpl("title").layout(fakeCtx(), slide, env("9:16", slide));
  assert.equal(L.k, 1.3);
});

test("step times: reveal times as given, a missing one follows at the calm pace, else automatic", () => {
  assert.deepEqual(S.stepTimes(3, [1, 2.5, 4]), [1, 2.5, 4]);
  assert.deepEqual(S.stepTimes(3, [1]).map((v) => Math.round(v * 100) / 100), [1, 1.7, 2.4]);
  assert.deepEqual(S.stepTimes(2, null).map((v) => Math.round(v * 100) / 100), [0.5, 1.2]);
  assert.deepEqual(S.stepTimes(0, [1]), []);
});

for (const n of ["bullets", "steps", "timeline", "bar_chart", "compare"]) {
  test(`${n}: a build step appears at its reveal time, not before`, () => {
    const reveal = [0.4, 2.0, 3.5, 5.0, 6.0, 7.0];
    const slide = { template: n, content: CONTENT[n], area: areas("9:16").slide_full[0], reveal };
    const e = env("9:16", slide);
    const L = tpl(n).layout(fakeCtx(), slide, e);
    const runs = { bullets: () => L.items, steps: () => L.runs, timeline: () => L.bodies, bar_chart: () => L.values, compare: () => L.cols.map((c) => c.items[0]) }[n]();
    const first = runs[1];
    const word = first.rows[0].text;
    const at = (t) => {
      const texts = [];
      tpl(n).draw(fakeCtx([], texts), t, slide, e, L);
      return texts.some((x) => x.text === word);
    };
    assert.equal(at(1.9), false, "before its reveal time");
    assert.equal(at(2.0 + S.enterTime(e.tokens) + 0.6), true, "after it arrived");
  });
}

test("big_number counts up and never past its value; without count it shows the value at once", () => {
  const slide = { template: "big_number", content: CONTENT.big_number, area: areas("9:16").slide_full[0] };
  const e = env("9:16", slide);
  const L = tpl("big_number").layout(fakeCtx(), slide, e);
  const shown = (t, s = slide) => {
    const texts = [];
    tpl("big_number").draw(fakeCtx([], texts), t, s, e, L);
    return texts[0] && texts[0].text;
  };
  const n = (s) => Number(String(s).replace(/\./g, ""));
  const seq = [0.5, 0.8, 1.2, 1.6, 2.4, 5].map((t) => n(shown(t)));
  for (let i = 1; i < seq.length; i++) assert.ok(seq[i] >= seq[i - 1], `monotone ${seq}`);
  assert.ok(seq[0] < 12000);
  assert.equal(shown(5), "12.000");
  const still = { ...slide, content: { ...CONTENT.big_number, count: false } };
  assert.equal(shown(0.5, still), "12.000");
});

test("a cut brand shows every element at once, a calm entrance takes longer than the brand spring", () => {
  const cut = tokens("cut");
  assert.equal(S.enterP(cut, 0.01), 1);
  assert.equal(S.enterTime(cut), 0);
  assert.ok(S.enterTime(tokens()) > 0.3);
});

test("palette: muted text only where it reads, the accent as text only from 3:1", () => {
  const t = tokens();
  t.colors = { ...t.colors, bg: "#F4F4F4", text: "#141414", muted: "#EEEEEE", accent: "#DDDDDD" };
  const p = S.palette(t);
  assert.equal(p.muted, "#141414");
  assert.equal(p.accentText, "#141414");
  assert.equal(p.accent, "#DDDDDD");
});

test("numbers in the job language", () => {
  assert.equal(S.formatValue(12000, "de"), "12.000");
  assert.equal(S.formatValue(4.5, "de"), "4,5");
  assert.equal(S.formatValue(12000.5, "en"), "12,000.5");
  assert.equal(S.quoted("x", "de"), "„x“");
});

test("image: redact boxes are burnt into a copy before any frame; malformed ones fail", () => {
  const fakeCanvas = (w, h) => {
    const ctx = fakeCtx();
    return { width: w, height: h, ctx, getContext: () => ctx };
  };
  const page = { width: 1920, height: 1080 };
  const base = { template: "image", content: CONTENT.image, area: [0, 0, 960, 540] };
  const e = env("16:9", base, { image: () => page, canvas: fakeCanvas });
  const plain = tpl("image").layout(fakeCtx(), base, e);
  assert.equal(plain.img, page);
  const slide = { ...base, redact: [[0.1, 0.1, 0.4, 0.2]], redact_style: "block" };
  const L = tpl("image").layout(fakeCtx(), slide, e);
  assert.notEqual(L.img, page);
  const ops = L.img.ctx.log.map((o) => o[0]);
  assert.ok(ops.includes("fillRect") && ops.indexOf("drawImage") < ops.indexOf("fillRect"));
  const log = [];
  tpl("image").draw(fakeCtx(log), 3, slide, e, L);
  const drawn = log.filter((o) => o[0] === "drawImage").map((o) => o[1]);
  assert.ok(drawn.length > 0 && drawn.every((x) => x === L.img), "frames draw only the copy");
  assert.throws(() => tpl("image").layout(fakeCtx(), { ...base, redact: "all" }, e), /redact/);
  assert.throws(() => tpl("image").layout(fakeCtx(), { ...base, redact: [[0.5, 0, 0.1, 1]] }, e), /redact\[0\]/);
  assert.throws(() => tpl("image").layout(fakeCtx(), { ...slide, redact_style: "crop" }, e), /redact_style/);
});
