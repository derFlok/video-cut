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
  // read-only for the scene: it can neither empty nor replace the list the renderer reads
  Object.defineProperty(window, "__vcBlocked", { get: () => blocked.slice(), configurable: false });

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
  for (const name of ("fetch XMLHttpRequest WebSocket EventSource Worker SharedWorker WebTransport " +
    "RTCPeerConnection webkitRTCPeerConnection RTCDataChannel RTCSessionDescription RTCIceCandidate").split(" "))
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

  // --- motion style (brand profile `motion` section; interface 1) ----------------------
  // theme dark|light|auto, depth flat|soft|glow, radius (share of the unit), energy
  // calm|normal|punchy, type_scale. Resolved like src/videocut/brand.py motion_style() when a
  // job's tokens predate these fields. Every colour below is derived from brand colours.
  const ms0 = tok.motion || {};
  const chan = (hex) => {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || "").trim());
    return m ? [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16)) : null;
  };
  const hx = (c) => "#" + c.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");
  const mixc = (a, b, k) => hx(chan(a).map((v, i) => v + (chan(b)[i] - v) * k));
  const alpha = (hex, a) => hx(chan(hex)) + Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, "0");
  const L = (hex) => lum(hx(chan(hex)));
  const cr = (a, b) => ratio(L(a), L(b));
  const EXTREME = [hx([0, 0, 0]), hx([255, 255, 255])];
  // `fg` itself when it reaches `min` against `bg`, else the nearest darker/lighter variant.
  function guard(fg, bg, min) {
    if (cr(fg, bg) >= min) return hx(chan(fg));
    const target = cr(EXTREME[0], bg) >= cr(EXTREME[1], bg) ? EXTREME[0] : EXTREME[1];
    for (let k = 1; k <= 100; k++) {
      const c = mixc(fg, target, k / 100);
      if (cr(c, bg) >= min) return c;
    }
    return target;
  }
  const pick = (v, allowed, dflt) => {
    const hit = String(v || "").toLowerCase().match(/[a-z]+/g) || [];
    return hit.find((w) => allowed.includes(w)) || dflt;
  };
  const brandDark = L(col.bg || EXTREME[1]) < 0.18;
  const themeRaw = pick(ms0.theme, ["dark", "light", "auto"], "auto");
  const theme = themeRaw === "auto" ? (brandDark ? "dark" : "light") : themeRaw;
  const depth = pick(ms0.depth, ["flat", "soft", "glow"], theme === "dark" ? "glow" : "soft");
  const charEnergy = { ruhig: "calm", federnd: "normal", knackig: "punchy" }[pick(ms0.character, ["ruhig", "federnd", "knackig"], "")];
  const energy = pick(ms0.energy, ["calm", "normal", "punchy"], charEnergy || "normal");
  const num = (v, lo, hi, d) => (Number.isFinite(v) && v >= lo && v <= hi ? v : d);
  const radiusK = num(ms0.radius, 0, 0.12, 0.028);
  const typeScale = num(ms0.type_scale, 0.6, 1.6, 1);
  const ENERGY = {
    calm: { dur: 1.2, beat: 0.5, overshoot: 0.7, drift: 0.6 },
    normal: { dur: 1, beat: 0.4, overshoot: 1, drift: 1 },
    punchy: { dur: 0.8, beat: 0.3, overshoot: 1.35, drift: 1.4 },
  }[energy];

  // Theme palette: the brand's own surface/text when the theme matches the brand, else the
  // brand's dark pair (colors.dark/dark_text) or text and background swapped.
  const surf = vars["--vc-surface"];
  const text = vars["--vc-text"];
  let panel, ink;
  if ((theme === "dark") === (L(surf) < 0.18)) [panel, ink] = [surf, text];
  else if (theme === "dark") [panel, ink] = col.dark && L(col.dark) < 0.18 ? [col.dark, col.dark_text || surf] : [text, surf];
  else [panel, ink] = [text, L(col.bg) < L(surf) ? col.bg : surf];
  panel = hx(chan(panel));
  ink = guard(ink, panel, 7);
  const accent = hx(chan(vars["--vc-accent"]));
  const accentText = guard(accent, panel, 4.5);
  const onAccent = guard(cr(accent, panel) > cr(accent, ink) ? panel : ink, accent, 4.5);
  const muted = guard(mixc(ink, panel, 0.38), panel, 3);
  const panel2 = mixc(panel, ink, theme === "dark" ? 0.07 : 0.045);
  const line = mixc(panel, ink, theme === "dark" ? 0.16 : 0.12);
  const shade = L(text) < L(surf) ? text : surf; // the brand's darkest base colour
  const sh = (k) => `${Math.round(k * spec.unit * 10) / 10}px`;
  const shadows = {
    flat: [`0 0 0 ${sh(0.0018)} ${line}`, `0 0 0 ${sh(0.0018)} ${line}`],
    soft: [
      `0 ${sh(0.004)} ${sh(0.01)} ${alpha(shade, 0.1)}, 0 ${sh(0.018)} ${sh(0.05)} ${alpha(shade, 0.16)}, 0 0 0 ${sh(0.0012)} ${alpha(shade, 0.06)}`,
      `0 ${sh(0.008)} ${sh(0.02)} ${alpha(shade, 0.14)}, 0 ${sh(0.04)} ${sh(0.1)} ${alpha(shade, 0.24)}`,
    ],
    glow: [
      `0 0 0 ${sh(0.0018)} ${alpha(accent, 0.45)}, 0 0 ${sh(0.03)} ${alpha(accent, 0.32)}, 0 0 ${sh(0.09)} ${alpha(accent, 0.16)}, 0 ${sh(0.02)} ${sh(0.06)} ${alpha(shade, 0.45)}`,
      `0 0 0 ${sh(0.0025)} ${alpha(accent, 0.7)}, 0 0 ${sh(0.05)} ${alpha(accent, 0.5)}, 0 0 ${sh(0.16)} ${alpha(accent, 0.25)}, 0 ${sh(0.03)} ${sh(0.09)} ${alpha(shade, 0.5)}`,
    ],
  }[depth];
  Object.assign(vars, {
    "--vc-panel": panel,
    "--vc-panel-2": panel2,
    "--vc-ink": ink,
    "--vc-ink-muted": muted,
    "--vc-line": line,
    "--vc-hi": accent,
    "--vc-hi-text": accentText,
    "--vc-on-hi": onAccent,
    "--vc-glow": alpha(accent, depth === "glow" ? 0.55 : depth === "soft" ? 0.28 : 0),
    "--vc-glow-soft": alpha(accent, depth === "glow" ? 0.22 : depth === "soft" ? 0.1 : 0),
    "--vc-hi-soft": alpha(accent, theme === "dark" ? 0.2 : 0.14),
    "--vc-ink-a10": alpha(ink, 0.1),
    "--vc-ink-a0": alpha(ink, 0),
    "--vc-sheen": theme === "dark" ? alpha(ink, 0.09) : alpha(accent, 0.07),
    "--vc-panel-a0": alpha(panel, 0),
    "--vc-panel-a80": alpha(panel, 0.82),
    "--vc-shadow": shadows[0],
    "--vc-shadow-lg": shadows[1],
    "--vc-text-glow": depth === "glow" ? `0 0 ${sh(0.02)} ${alpha(accent, 0.65)}, 0 0 ${sh(0.06)} ${alpha(accent, 0.3)}` : "none",
    "--vc-radius": `${Math.round(radiusK * spec.unit)}px`,
    "--vc-radius-sm": `${Math.round(radiusK * 0.5 * spec.unit)}px`,
    "--vc-ts": String(typeScale),
  });
  for (const [k, v] of Object.entries(vars)) if (v != null) root.style.setProperty(k, v);

  // Brand spring (w = natural frequency, z = damping ratio) as an anime.js spring; energy
  // makes it quicker and bouncier (punchy) or softer (calm). `pop` overshoots more.
  const sp = ms0.spring || {};
  const w0 = Number.isFinite(sp.w) ? sp.w : 13;
  const z0 = Number.isFinite(sp.z) ? sp.z : 0.8;
  const w = w0 / ENERGY.dur;
  const z = Math.max(0.3, Math.min(1, z0 / Math.sqrt(ENERGY.overshoot)));
  const mkSpring = (ww, zz) => (A.spring || A.createSpring)({ mass: 1, stiffness: ww * ww, damping: 2 * zz * ww, velocity: 0 });
  const spring = mkSpring(w, z);
  const springPop = mkSpring(w * 1.15, Math.max(0.3, z * 0.62));
  const style = {
    theme,
    theme_raw: themeRaw,
    depth,
    energy,
    radius: radiusK,
    type_scale: typeScale,
    // durations scale (1 = normal), the micro-event interval (s), overshoot factor
    dur: ENERGY.dur,
    beat: ENERGY.beat,
    overshoot: ENERGY.overshoot,
    drift: ENERGY.drift,
    colors: { panel, panel2, ink, muted, line, accent, accentText, onAccent },
    contrast: {
      ink_on_panel: Math.round(cr(ink, panel) * 100) / 100,
      accent_text_on_panel: Math.round(cr(accentText, panel) * 100) / 100,
      on_accent: Math.round(cr(onAccent, accent) * 100) / 100,
      muted_on_panel: Math.round(cr(muted, panel) * 100) / 100,
    },
  };

  // --- cues (interface 2): what happened when, for the sound-effects track --------------
  const CUE_KINDS = ["pop", "whoosh", "tick", "check", "strike", "type", "count", "impact", "swoosh_out"];
  const cues = [];
  function cue(kind, t, opt = {}) {
    if (!CUE_KINDS.includes(kind)) throw new Error(`cue kind "${kind}" is not one of ${CUE_KINDS.join(", ")}`);
    if (!Number.isFinite(t)) throw new Error(`cue "${kind}": time must be a number`);
    const strength = Number.isFinite(opt.strength) ? Math.max(0, Math.min(1, opt.strength)) : 0.6;
    if (t >= 0 && t <= spec.duration) cues.push({ t: Math.round(t * 1000) / 1000, kind, component: String(opt.component || "scene"), strength });
  }

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
  const base = document.createElement("style");
  base.textContent = `
    *, *::before, *::after { box-sizing: border-box; transition: none !important; }
    html, body { margin: 0; padding: 0; background: none; overflow: hidden; }
    body { width: ${spec.size[0]}px; height: ${spec.size[1]}px; position: relative;
      font-family: var(--vc-font); color: var(--vc-text); letter-spacing: var(--vc-track);
      -webkit-font-smoothing: antialiased; text-rendering: geometricPrecision; }
    #vc-stage { position: absolute; }
  `;
  document.head.appendChild(base);
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
    style,
    spring,
    springPop,
    cue,
    CUE_KINDS,
    THREE: null,
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
      const svgTags = ["svg", "path", "line", "rect", "circle", "polyline", "polygon", "g", "defs", "linearGradient", "stop", "clipPath", "ellipse", "text"];
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
    load: async (url, threeUrl) => {
      if (threeUrl) vc.THREE = await import(threeUrl);
      const mod = await import(url);
      if (typeof mod.default !== "function") throw new Error("scene module must export default function (vc)");
      await document.fonts.ready;
      mod.default(vc);
      tl.seek(0);
      seek(0);
      return { duration_ms: tl.duration, blocked: blocked.slice(), style };
    },
    // Cues sorted by time (scene seconds).
    cues: () => cues.slice().sort((a, b) => a.t - b.t || a.kind.localeCompare(b.kind)),
  };
})();
