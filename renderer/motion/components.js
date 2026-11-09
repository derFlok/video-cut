// Motion components for scene modules (classic script, runs in the sandboxed page).
// Every colour and font comes from the brand tokens through the CSS custom properties the
// runtime sets (--vc-*); sizes are shares of --vc-unit (the frame's short side).
// Each component builds its DOM under `parent`, puts its tweens on vc.tl at the times it
// is given (scene seconds, usually vc.at("word") or vc.beat("id")) and returns its root
// element. `out` (optional) is when it leaves.
window.__vcComponents = function (vc) {
  "use strict";
  const A = vc.anime;
  const U = vc.unit;
  const ms = vc.ms;
  const px = (k) => `${Math.round(k * U)}px`;

  vc.css(`
    .vc-card { position: relative; background: var(--vc-surface); color: var(--vc-text);
      border-radius: ${px(0.034)}; padding: ${px(0.036)} ${px(0.044)}; }
    .vc-card { box-shadow: 0 ${px(0.014)} ${px(0.05)} color-mix(in srgb, var(--vc-text) 16%, var(--vc-surface) 0%); }
    .vc-label { font-weight: var(--vc-w-mid); color: var(--vc-muted); font-size: ${px(0.04)}; letter-spacing: 0; }
    .vc-head { display: flex; flex-wrap: wrap; justify-content: center; align-items: baseline;
      gap: ${px(0.012)} ${px(0.022)}; font-weight: var(--vc-w-strong); line-height: 1.08; text-align: center; }
    .vc-head .w { position: relative; display: inline-block; padding: 0 ${px(0.008)}; }
    .vc-head .w > .t { position: relative; z-index: 1; }
    .vc-head .mark { position: absolute; left: 0; right: 0; transform-origin: 0 50%; z-index: 0; }
    .vc-head .mark.box { top: 4%; bottom: -2%; background: var(--vc-accent); border-radius: ${px(0.012)}; }
    .vc-head .mark.underline { height: 0.12em; bottom: -0.04em; background: var(--vc-accent); border-radius: 0.06em; }
    .vc-count { display: flex; align-items: baseline; gap: ${px(0.02)}; }
    .vc-count .digits { display: flex; font-weight: var(--vc-w-strong); font-variant-numeric: tabular-nums; line-height: 1; }
    .vc-count .col { height: 1.2em; overflow: hidden; position: relative; }
    .vc-count .reel { display: flex; flex-direction: column; }
    .vc-count .reel > span { height: 1.2em; line-height: 1.2em; display: block; text-align: center; }
    .vc-count .side { white-space: nowrap; }
    .vc-count .suffix { font-weight: var(--vc-w-strong); }
    .vc-term { background: var(--vc-dark); color: var(--vc-dark-text); padding: 0; overflow: hidden; }
    .vc-term .bar { display: flex; gap: ${px(0.014)}; padding: ${px(0.022)} ${px(0.03)}; }
    .vc-term .bar i { width: ${px(0.018)}; height: ${px(0.018)}; border-radius: 50%; background: var(--vc-muted); opacity: 0.6; }
    .vc-term .body { padding: ${px(0.006)} ${px(0.04)} ${px(0.04)}; font-size: ${px(0.036)}; line-height: 1.45;
      font-variant-numeric: tabular-nums; white-space: pre-wrap; letter-spacing: 0; }
    .vc-term .p { opacity: 0.55; font-weight: var(--vc-w-strong); }
    .vc-term .out { opacity: 0.72; }
    .vc-term .caret { display: inline-block; width: 0.55em; height: 1.05em; vertical-align: -0.18em;
      background: var(--vc-dark-text); margin-left: 0.04em; }
    .vc-strike { position: relative; display: inline-block; }
    .vc-strike .line { position: absolute; left: -2%; right: -2%; top: 52%; height: 0.11em;
      background: var(--vc-accent); border-radius: 0.06em; transform-origin: 0 50%; }
    .vc-list .row { display: flex; align-items: center; gap: ${px(0.026)}; padding: ${px(0.012)} 0; }
    .vc-list .box { flex: none; width: ${px(0.062)}; height: ${px(0.062)}; border-radius: ${px(0.016)};
      position: relative; border: ${px(0.005)} solid var(--vc-muted); }
    .vc-list .fill { position: absolute; inset: ${px(-0.005)}; border-radius: inherit; background: var(--vc-accent); transform: scale(0); }
    .vc-list svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
    .vc-list path { fill: none; stroke: var(--vc-on-accent); stroke-width: 3.2; stroke-linecap: round; stroke-linejoin: round; }
    .vc-list .txt { font-weight: var(--vc-w-mid); font-size: ${px(0.05)}; line-height: 1.15; white-space: nowrap; }
    .vc-head.vc-card { padding: ${px(0.026)} ${px(0.044)}; width: fit-content; max-width: 100%; }
  `);

  // A style value that switches at scene time `at` (anime.js tweens numbers, not tokens).
  function setAt(el, prop, value, at) {
    vc.onSeek((t) => (el.style[prop] = t >= at ? value : ""));
  }

  const hide = (el) => {
    el.style.opacity = "0";
    return el;
  };

  // Enter: fade, slight scale and blur in, a short rise; the brand spring.
  function pop(el, t, opt = {}) {
    hide(el);
    const rise = opt.rise == null ? 0.03 : opt.rise;
    vc.tl.add(
      el,
      {
        opacity: { from: 0, to: 1, ease: "out(3)", duration: 260 },
        scale: { from: opt.scale || 0.9, to: 1 },
        y: { from: rise * U, to: 0 },
        filter: { from: `blur(${(opt.blur == null ? 0.014 : opt.blur) * U}px)`, to: "blur(0px)", ease: "out(3)", duration: 320 },
      },
      ms(t),
    );
    return el;
  }

  // Leave: blur out and shrink a little, faster than the entrance.
  function leave(el, t, opt = {}) {
    vc.tl.add(
      el,
      {
        opacity: { to: 0, ease: "in(2)", duration: 240 },
        scale: { to: opt.scale || 0.94, ease: "in(2)", duration: 260 },
        y: { to: (opt.rise == null ? -0.02 : -opt.rise) * U, ease: "in(2)", duration: 260 },
        filter: { to: `blur(${0.012 * U}px)`, ease: "in(2)", duration: 260 },
      },
      ms(t),
    );
    return el;
  }

  // Continuous, slow motion while something is on screen (nothing ever stands still).
  function drift(el, t0, t1, amount = 0.025) {
    vc.tl.add(el, { scale: { from: 1, to: 1 + amount }, ease: "linear", duration: ms(t1 - t0), composition: "blend" }, ms(t0));
    return el;
  }

  // Kinetic headline: words build one by one on their spoken times; `mark` boxes or
  // underlines words in the accent. words: [{text, at}], mark: [{index, at, style: "box"|"underline"}]
  // `card: true` sets it on a surface card (legible over any picture).
  function headline(parent, { words, mark = [], size = 0.088, out, card = false } = {}) {
    const root = vc.el("div", card ? "vc-head vc-card" : "vc-head", parent);
    if (card) pop(root, words[0].at - 0.15, { scale: 0.94 });
    root.style.fontSize = px(size);
    const spans = words.map((w) => {
      const s = vc.el("span", "w", root);
      vc.el("span", "t", s, w.text);
      pop(s, w.at, { scale: 0.7, rise: 0.04, blur: 0.02 });
      return s;
    });
    for (const m of mark) {
      const s = spans[m.index];
      const bar = vc.el("span", `mark ${m.style || "box"}`, s);
      bar.style.transform = "scaleX(0)";
      vc.tl.add(bar, { scaleX: { from: 0, to: 1 }, ease: "out(4)", duration: 380 }, ms(m.at));
      if ((m.style || "box") === "box") setAt(s.firstChild, "color", "var(--vc-on-accent)", m.at + 0.12);
      vc.tl.add(s, { scale: [{ to: 1.08, duration: 160, ease: "out(2)" }, { to: 1, duration: 260, ease: "out(2)" }] }, ms(m.at));
    }
    if (out != null) leave(root, out);
    return root;
  }

  // Counter: rolls from `from` to `to` (odometer digits) starting at `at`.
  function counter(parent, { to, from = 0, at, dur = 0.9, label, suffix = "", size = 0.2, enter, out } = {}) {
    const root = vc.el("div", "vc-card vc-count", parent);
    const digits = vc.el("div", "digits", root);
    digits.style.fontSize = px(size);
    const n = Math.max(String(Math.abs(Math.round(to))).length, String(Math.abs(Math.round(from))).length);
    const cols = [];
    for (let k = n - 1; k >= 0; k--) {
      const col = vc.el("div", "col", digits);
      const reel = vc.el("div", "reel", col);
      for (let d = 0; d <= 10; d++) vc.el("span", "", reel, String(d % 10));
      cols.push({ k, reel });
    }
    const side = vc.el("div", "side", root);
    if (suffix) {
      const s = vc.el("div", "suffix", side, suffix);
      s.style.fontSize = px(size * 0.42);
    }
    if (label) vc.el("div", "vc-label", side, label);
    const ease = A.eases.out(4);
    let digitW = 0;
    vc.onSeek((t) => {
      if (!digitW) digitW = Math.max(...cols.map((c) => c.reel.offsetWidth));
      const p = Math.max(0, Math.min(1, (t - at) / dur));
      const v = from + (to - from) * ease(p);
      for (const { k, reel } of cols) {
        const place = 10 ** k;
        // Odometer: a digit turns over while the digit below rolls its last unit.
        const pos = k === 0 ? v % 10 : (Math.floor(v / place) % 10) + Math.max(0, (v % place) - (place - 1));
        reel.style.transform = `translateY(${-pos * 1.2}em)`;
        // A leading digit shows up (and takes its width) as the value reaches its place.
        const on = k === 0 ? 1 : Math.max(0, Math.min(1, v - (place - 1)));
        reel.parentElement.style.opacity = String(on);
        reel.parentElement.style.width = `${(on * digitW).toFixed(2)}px`;
      }
    });
    pop(root, enter == null ? at - 0.25 : enter);
    if (out != null) leave(root, out);
    return root;
  }

  // Terminal: lines typed at `cps` characters per second from `at`; `out: true` lines
  // appear at once (program output). lines: [{text, at, cps, prompt, out}]
  function terminal(parent, { lines, enter, out, width = 0.84 } = {}) {
    const root = vc.el("div", "vc-card vc-term", parent);
    root.style.width = px(width);
    const bar = vc.el("div", "bar", root);
    for (let i = 0; i < 3; i++) vc.el("i", "", bar);
    const body = vc.el("div", "body", root);
    const rows = lines.map((l) => {
      const row = vc.el("div", l.out ? "out" : "", body);
      row.style.display = "none";
      const p = l.out || l.prompt === false ? null : vc.el("span", "p", row, "$ ");
      const txt = vc.el("span", "", row);
      return { l, row, p, txt };
    });
    const caret = vc.el("span", "caret", null);
    vc.onSeek((t) => {
      let last = null;
      for (const r of rows) {
        const cps = r.l.cps || 28;
        const shown = r.l.out ? (t >= r.l.at ? r.l.text.length : 0) : Math.max(0, Math.min(r.l.text.length, Math.floor((t - r.l.at) * cps)));
        r.row.style.display = t >= r.l.at ? "" : "none";
        r.txt.textContent = r.l.text.slice(0, shown);
        if (t >= r.l.at) last = r;
      }
      if (last) last.row.appendChild(caret);
      const typing = rows.some((r) => !r.l.out && t >= r.l.at && t < r.l.at + r.l.text.length / (r.l.cps || 28));
      caret.style.opacity = typing || Math.floor(t * 2.2) % 2 === 0 ? "1" : "0";
    });
    pop(root, enter == null ? lines[0].at - 0.3 : enter);
    if (out != null) leave(root, out);
    return root;
  }

  // Card with a strike-through: `text` is struck at `strikeAt` and fades to muted;
  // `then` (optional) is the correction that pops in after it.
  function strikeCard(parent, { label, text, at, strikeAt, then, thenAt, size = 0.07, out } = {}) {
    const root = vc.el("div", "vc-card", parent);
    root.style.textAlign = "center";
    if (label) vc.el("div", "vc-label", root, label);
    const wrap = vc.el("div", "vc-strike", root);
    wrap.style.fontSize = px(size);
    wrap.style.fontWeight = "var(--vc-w-strong)";
    const word = vc.el("span", "", wrap, text);
    const line = vc.el("span", "line", wrap);
    line.style.transform = "scaleX(0)";
    vc.tl.add(line, { scaleX: { from: 0, to: 1 }, ease: "inOut(3)", duration: 420 }, ms(strikeAt));
    vc.tl.add(word, { opacity: { from: 1, to: 0.45 }, ease: "linear", duration: 300 }, ms(strikeAt + 0.2));
    vc.tl.add(wrap, { scale: { to: 0.86 }, ease: "out(3)", duration: 400 }, ms(strikeAt + 0.25));
    if (then) {
      const fix = vc.el("div", "", root, then);
      fix.style.fontSize = px(size);
      fix.style.fontWeight = "var(--vc-w-strong)";
      fix.style.color = "var(--vc-accent-text)";
      pop(fix, thenAt == null ? strikeAt + 0.45 : thenAt, { scale: 0.8 });
    }
    pop(root, at);
    if (out != null) leave(root, out);
    return root;
  }

  // Checklist: items appear at `at` and are checked at `check` (the check stroke draws,
  // the box fills in the accent, the row gets a small nudge).
  // items: [{text, at, check}]; `columns` lays the rows out in a grid.
  function checklist(parent, { label, items, enter, out, size = 0.05, columns = 1 } = {}) {
    const root = vc.el("div", "vc-card vc-list", parent);
    if (label) {
      const l = vc.el("div", "vc-label", root, label);
      l.style.marginBottom = px(0.01);
    }
    const grid = vc.el("div", "", root);
    if (columns > 1) Object.assign(grid.style, { display: "grid", gridTemplateColumns: `repeat(${columns}, auto)`, columnGap: px(0.05) });
    for (const it of items) {
      const row = vc.el("div", "row", grid);
      const box = vc.el("div", "box", row);
      const fill = vc.el("div", "fill", box);
      const svg = vc.el("svg", "", box);
      svg.setAttribute("viewBox", "0 0 24 24");
      const path = vc.el("path", "", svg);
      path.setAttribute("d", "M6 12.5l4 4 8-9");
      path.setAttribute("pathLength", "1");
      path.style.strokeDasharray = "1";
      path.style.strokeDashoffset = "1";
      const txt = vc.el("div", "txt", row, it.text);
      txt.style.fontSize = px(size);
      pop(row, it.at, { scale: 0.96, rise: 0.02 });
      if (it.check != null) {
        vc.tl.add(fill, { scale: { from: 0, to: 1 } }, ms(it.check));
        setAt(box, "borderColor", "var(--vc-accent)", it.check);
        vc.tl.add(path, { strokeDashoffset: { from: 1, to: 0 }, ease: "out(3)", duration: 300 }, ms(it.check + 0.08));
        vc.tl.add(row, { x: [{ to: 0.012 * U, duration: 140, ease: "out(2)" }, { to: 0, duration: 300, ease: "out(2)" }] }, ms(it.check));
      }
    }
    pop(root, enter == null ? items[0].at - 0.2 : enter);
    if (out != null) leave(root, out);
    return root;
  }

  return { setAt, pop, leave, drift, headline, counter, terminal, strikeCard, checklist };
};
