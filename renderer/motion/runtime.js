// Page runtime of a motion scene (classic script, runs in the sandboxed page before the
// scene module). It makes the page a pure function of virtual time:
//   - the clock is virtual: Date.now / performance.now return the seek time, timers and
//     requestAnimationFrame never fire, Math.random is seeded;
//   - CSS transitions are off, CSS/WAAPI animations are paused and seeked with the scene;
//   - network APIs throw (the page's CSP and the renderer's request filter block them too).
// The scene module (`export default function (vc) {...}`) builds DOM/SVG under vc.stage
// and puts its tweens on vc.tl (an anime.js timeline in ms, never played, only seeked).
// window.__vc.seek(t) sets every visible property for scene time t (seconds).
(function () {
  "use strict";
  const spec = window.__vcSpec;
  const A = window.anime;
  const blocked = [];
  window.__vcBlocked = blocked;

  // --- determinism -------------------------------------------------------------------
  let now = 0;
  Date.now = () => Math.round(now * 1000);
  performance.now = () => now * 1000;
  window.requestAnimationFrame = () => 0;
  window.cancelAnimationFrame = () => {};
  window.setTimeout = () => 0;
  window.setInterval = () => 0;
  window.clearTimeout = () => {};
  window.clearInterval = () => {};
  const rand = A.createSeededRandom(Number.isInteger(spec.seed) ? spec.seed : 1);
  Math.random = () => rand(0, 1, 6);

  // --- no network (defence in depth: CSP and the request filter come first) ------------
  const deny = (name) =>
    function () {
      blocked.push(name);
      throw new Error(`sandbox: ${name} is not available in a motion scene`);
    };
  for (const name of "fetch XMLHttpRequest WebSocket EventSource Worker SharedWorker WebTransport RTCPeerConnection".split(" "))
    Object.defineProperty(window, name, { value: deny(name), writable: false, configurable: false });
  Object.defineProperty(navigator, "sendBeacon", { value: deny("sendBeacon") });
  document.addEventListener("securitypolicyviolation", (e) => blocked.push(`csp:${e.violatedDirective}:${e.blockedURI}`));

  // --- brand tokens as CSS custom properties -------------------------------------------
  const tok = spec.tokens || {};
  const col = tok.colors || {};
  const cap = tok.caption_colors || {};
  const fonts = tok.fonts || {};
  const ws = (fonts.weights || []).filter(Number.isFinite).sort((a, b) => a - b);
  const strong = ws.length ? ws[ws.length - 1] : 700;
  const regular = ws.length ? ws[0] : 400;
  const mid = ws.find((w) => w >= 600) || strong;
  const track = (fonts.tracking && fonts.tracking.display) || 0;
  const vars = {
    "--vc-bg": col.bg,
    "--vc-surface": col.surface || col.bg,
    "--vc-text": col.text,
    "--vc-muted": col.muted || col.text,
    "--vc-accent": col.accent || col.text,
    // accent as text on a surface: the contrast-checked caption highlight
    "--vc-accent-text": cap.highlight || col.accent || col.text,
    "--vc-dark": col.dark || col.text,
    "--vc-dark-text": col.dark_text || col.surface || col.bg,
    "--vc-font": spec.fontFamily,
    "--vc-w-regular": String(regular),
    "--vc-w-mid": String(mid),
    "--vc-w-strong": String(strong),
    "--vc-track": `${track}em`,
    "--vc-unit": `${spec.unit}px`,
  };
  // Text on an accent fill: the brand text or surface colour, whichever contrasts more.
  const lum = (hex) => {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
    if (!m) return null;
    const c = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  };
  const ratio = (a, b) => (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
  const la = lum(vars["--vc-accent"]);
  const lt = lum(vars["--vc-text"]);
  const ls = lum(vars["--vc-surface"]);
  vars["--vc-on-accent"] = la != null && lt != null && ls != null && ratio(la, ls) > ratio(la, lt) ? vars["--vc-surface"] : vars["--vc-text"];
  const root = document.documentElement;
  for (const [k, v] of Object.entries(vars)) if (v != null) root.style.setProperty(k, v);

  // Brand spring (w = natural frequency, z = damping ratio) as an anime.js spring.
  const sp = (tok.motion && tok.motion.spring) || {};
  const w = Number.isFinite(sp.w) ? sp.w : 13;
  const z = Number.isFinite(sp.z) ? sp.z : 0.8;
  const spring = A.createSpring({ mass: 1, stiffness: w * w, damping: 2 * z * w, velocity: 0 });

  // --- words ---------------------------------------------------------------------------
  const norm = (s) => String(s).toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
  const words = (spec.words || []).map((x) => ({ w: x.w, t0: x.t0, t1: x.t1, n: norm(x.w) }));
  // Time of the n-th occurrence (1-based) of a word or phrase; `end` gives its end time.
  function find(phrase, n = 1) {
    const parts = String(phrase).split(/\s+/).map(norm).filter(Boolean);
    let seen = 0;
    for (let i = 0; i + parts.length <= words.length; i++) {
      if (parts.every((p, k) => words[i + k].n === p) && ++seen === n) return { t0: words[i].t0, t1: words[i + parts.length - 1].t1 };
    }
    throw new Error(`word "${phrase}" (#${n}) is not spoken in this scene`);
  }
  const beats = spec.beats || {};

  // --- stage ---------------------------------------------------------------------------
  const style = document.createElement("style");
  style.textContent = `
    *, *::before, *::after { box-sizing: border-box; transition: none !important; }
    html, body { margin: 0; padding: 0; background: none; overflow: hidden; }
    body { width: ${spec.size[0]}px; height: ${spec.size[1]}px; position: relative;
      font-family: var(--vc-font); color: var(--vc-text); letter-spacing: var(--vc-track);
      -webkit-font-smoothing: antialiased; text-rendering: geometricPrecision; }
    #vc-stage { position: absolute; }
  `;
  document.head.appendChild(style);
  const stage = document.createElement("div");
  stage.id = "vc-stage";
  const [x0, y0, x1, y1] = spec.region;
  Object.assign(stage.style, { left: `${x0}px`, top: `${y0}px`, width: `${x1 - x0}px`, height: `${y1 - y0}px` });
  document.body.appendChild(stage);

  const tl = A.createTimeline({ autoplay: false, defaults: { ease: spring } });
  const updaters = [];

  const vc = {
    anime: A,
    tl,
    stage,
    size: spec.size.slice(),
    region: spec.region.slice(),
    unit: spec.unit,
    duration: spec.duration,
    fps: spec.fps,
    tokens: tok,
    spring,
    words: words.map(({ w, t0, t1 }) => ({ w, t0, t1 })),
    at: (phrase, n) => find(phrase, n).t0,
    end: (phrase, n) => find(phrase, n).t1,
    beat: (id) => {
      if (!(id in beats)) throw new Error(`beat "${id}" is not in the beat sheet`);
      return beats[id];
    },
    // Seconds -> timeline position (anime.js works in ms).
    ms: (t) => Math.round(t * 1000),
    // A pure function of scene time for what tweens cannot express (typed text, digits).
    onSeek: (fn) => updaters.push(fn),
    random: rand,
    el: (tag, cls, parent, text) => {
      const svgTags = ["svg", "path", "line", "rect", "circle", "polyline", "g"];
      const e = svgTags.includes(tag) ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
      if (cls) e.setAttribute("class", cls);
      if (text != null) e.textContent = text;
      if (parent) parent.appendChild(e);
      return e;
    },
    css: (text) => {
      const s = document.createElement("style");
      s.textContent = text;
      document.head.appendChild(s);
    },
  };
  vc.c = window.__vcComponents ? window.__vcComponents(vc) : {};

  function seek(t) {
    now = t;
    tl.seek(Math.max(0, t * 1000));
    for (const fn of updaters) fn(t);
    for (const a of document.getAnimations()) {
      a.pause();
      a.currentTime = t * 1000;
    }
    return blocked.length;
  }

  window.__vc = {
    vc,
    seek,
    // Load the scene module (same origin; a module that fails to load fails the render).
    load: async (url) => {
      const mod = await import(url);
      if (typeof mod.default !== "function") throw new Error("scene module must export default function (vc)");
      await document.fonts.ready;
      mod.default(vc);
      tl.seek(0);
      seek(0);
      return { duration_ms: tl.duration, blocked: blocked.slice() };
    },
  };
})();
