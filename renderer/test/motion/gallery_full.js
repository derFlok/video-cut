// Gallery of every motion component, one per 2.6 s slot, with neutral sample content
// (renderer/motion/CATALOGUE.md; `node scripts/motion_gallery.js` renders its contact sheet).
// Every slot's still is taken 2.2 s into the slot.
export const SLOT = 2.6;
export default function scene(vc) {
  const { c, stage } = vc;
  const at = (i, k = 0) => i * SLOT + 0.2 + k;
  const out = (i) => (i + 1) * SLOT - 0.15;
  const slots = [];
  const slot = (i) => (slots[i] = c.slot(stage));

  // 0: 3D field behind a kinetic headline with a glow mark
  const s0 = slot(0);
  c.field3d(s0, { at: at(0), out: out(0), kind: "grid" });
  c.headline(s0, {
    words: ["ship", "every", "week"].map((text, k) => ({ text, at: at(0, 0.15 + k * 0.3) })),
    mark: [{ index: 2, at: at(0, 1.2), style: "box" }],
    out: out(0),
  });
  // 1: stat with unit change and source
  c.stat(slot(1), { value: 1280, at: at(1, 0.3), dur: 0.9, unit: "units", unitTo: "/ day", unitAt: at(1, 1.5), label: "throughput", source: "sample data, 2026", out: out(1), sep: "," });
  // 2: bar chart, the last bar wins
  c.barChart(slot(2), {
    title: "per quarter",
    bars: [3, 5, 4, 9].map((value, k) => ({ label: `Q${k + 1}`, value, at: at(2, 0.3 + k * 0.3) })),
    highlight: 3, highlightAt: at(2, 1.6), unit: "k", out: out(2),
  });
  // 3: line / area chart
  c.lineChart(slot(3), { title: "growth", points: [2, 3, 2.6, 4.2, 6.1].map((v, k) => ({ v, at: at(3, 0.3 + k * 0.38) })), decimals: 1, unit: "x", out: out(3) });
  // 4: comparison
  c.compare(slot(4), {
    left: { title: "manual", items: ["slow", "error-prone"], icon: "clock" },
    right: { title: "automatic", items: ["fast", "repeatable"], icon: "bolt" },
    at: at(4, 0.1), winAt: at(4, 1.3), out: out(4),
  });
  // 5: steps
  c.steps(slot(5), { steps: ["record", "cut", "check", "publish"].map((title, k) => ({ title, sub: `step ${k + 1}`, at: at(5, 0.3 + k * 0.42) })), out: out(5) });
  // 6: table
  c.table(slot(6), {
    title: "results", columns: ["option", { text: "time", num: true }, { text: "cost", num: true }],
    rows: [["first", "4 h", "120"], ["second", "2 h", "80"], ["third", "20 min", "15"]].map((cells, k) => ({ cells, at: at(6, 0.3 + k * 0.4), hi: k === 2 })),
    out: out(6),
  });
  // 7: browser frame with a file list
  const b7 = c.frame(slot(7), { kind: "browser", url: "example.invalid/project", at: at(7), width: 0.8, out: out(7) });
  c.fileList(b7.body, { files: [["src", "folder", 0], ["index.html", "file", 1], ["app.js", "file", 1], ["notes.md", "file", 0]].map(([name, kind, indent], k) => ({ name, kind, indent, meta: `${k + 2} kB`, at: at(7, 0.35 + k * 0.3), hi: k === 2 })) });
  // 8: terminal
  c.terminal(slot(8), { lines: [{ text: "build --all", at: at(8, 0.3) }, { text: "3 files changed", at: at(8, 1.1), out: true }, { text: "deploy", at: at(8, 1.5) }], width: 0.8, out: out(8) });
  // 9: phone with a chat
  const s9 = slot(9);
  const ph = c.frame(s9, { kind: "phone", at: at(9), width: 0.42, height: 0.62, out: out(9) });
  c.chat(ph.body, { messages: [{ text: "does it work offline?", at: at(9, 0.6), side: "in" }, { text: "yes, all local", at: at(9, 1.4), side: "out" }] });
  // 10: app card with rating and a pressed button
  c.appCard(slot(10), { icon: "play", title: "sample app", meta: "4.8", rating: 5, ratingAt: at(10, 0.5), action: "open", actionAt: at(10, 1.6), at: at(10), width: 0.84, out: out(10) });
  // 11: meters
  const s11 = slot(11);
  c.meter(s11, { value: 72, at: at(11, 0.3), label: "done", out: out(11) });
  c.meter(s11, { value: 38, at: at(11, 0.6), label: "left", kind: "ring", out: out(11) });
  // 12: chips
  c.chips(slot(12), { chips: [["sea", "wave"], ["mountains", "mountain"], ["cycling", "bike"], ["golf", "flag"], ["paddle", "paddle"]].map(([text, icon], k) => ({ text, icon, at: at(12, 0.2 + k * 0.3) })), allAt: at(12, 1.9), width: 0.86, out: out(12) });
  // 13: quote
  c.quote(slot(13), { words: "make the boring part disappear".split(" ").map((text, k) => ({ text, at: at(13, 0.3 + k * 0.25), mark: k === 2 })), by: "sample speaker", width: 0.84, out: out(13) });
  // 14: pointer towards the speaker
  c.pointer(slot(14), { at: at(14, 0.2), label: "this one", out: out(14) });
  // 15: strike and swap
  c.strikeCard(slot(15), { label: "plan", text: "later", at: at(15, 0.1), strikeAt: at(15, 0.8), then: "now", thenAt: at(15, 1.3), out: out(15) });
  // 16: checklist with tally
  c.checklist(slot(16), { label: "ready?", items: ["sound", "light", "script"].map((text, k) => ({ text, at: at(16, 0.2 + k * 0.25), check: at(16, 0.9 + k * 0.35) })), out: out(16) });
  // 17: extruded 3D number, then a stat morphs out of it
  const s17 = slot(17);
  c.number3d(s17, { text: "42", at: at(17, 0.1), out: out(17) });
  // 18: tilted UI plane (CSS 3D camera move) with a window
  const s18 = slot(18);
  const w18 = c.frame(s18, { kind: "window", title: "settings", at: at(18), width: 0.7 });
  c.fileList(w18.body, { files: ["general", "export", "sound"].map((name, k) => ({ name, kind: "folder", at: at(18, 0.3 + k * 0.25), hi: k === 1 })) });
  c.tilt(w18, { at: at(18), until: out(18) });
  c.leave(w18, out(18));
  // 19: hand-over: a checklist morphs into a stat
  const s19 = slot(19);
  const l19 = c.checklist(s19, { items: ["a", "b"].map((text, k) => ({ text, at: at(19, 0.1 + k * 0.2), check: at(19, 0.3 + k * 0.2) })), micro: false });
  c.stat(c.slot(stage), { value: 2, at: at(19, 1.0), dur: 0.5, unit: "done", after: l19, enter: at(19, 0.9), out: out(19) });
}
