// Glass material (brand motion.depth "glass"): numbers and pixels shared by the canvas
// scenes (render.js bundles this file) and the motion runtime (motion.js serves it to the
// page as /rt/glass.js, where it sets window.__vcGlass).
//
// How glass is made: the renderers capture region-only with alpha, so the page never sees
// the video and CSS backdrop-filter has nothing to blur. Instead every frame also yields a
// GLASS MASK (the alpha of every glass shape at that moment, white on transparent, with the
// shape's own fade, scale and blur) written as a second clip; `render` blurs and saturates
// the video through that mask in FFmpeg (frost) and lays the overlay on top. The overlay
// itself carries only the tint (the brand panel colour at TINT alpha), the specular edge,
// the grain and the content.
//
// Vibrancy guard: `tint()` returns the lowest tint alpha (never below the material's own)
// at which the ink keeps 4.5:1 (the accent, used only for large text, 3:1) against the
// tint laid over the WORST backdrop: the darkest and the lightest frost measured under the
// glass in this job (tokens.glass_backdrop = [lmin, lmax], relative luminance, written by
// the pipeline from the video itself; pure black and white when there is no measurement).
// A denser tint than the material's own is reported as `densified`.
// No colour literals: colours are computed from brand hex tokens or as plain numbers.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.__vcGlass = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  // Frost: Gaussian sigma as a share of the frame's short side; the composite blurs at a
  // quarter of the size (the frost has no detail to lose), so this is ~35 px at 1080.
  const FROST_SIGMA = 0.032;
  const FROST_SATURATION = 1.5;
  const FROST_BRIGHTNESS = { dark: -0.04, light: 0.05 };
  // The material's own tint alpha (before the vibrancy guard): Apple's "regular" material.
  const TINT = { dark: 0.48, light: 0.55 };
  const MIN_CONTRAST = 4.5;
  const LARGE_CONTRAST = 3; // WCAG AA for large text: the accent words and figures


  function chan(hex) {
    const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(hex || "").trim());
    if (!m) return null;
    const h = m[1].length === 3 ? m[1].replace(/./g, (c) => c + c) : m[1];
    return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  }
  const hex = (c) => "#" + c.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");
  // `hexColor` at alpha `a` as #rrggbbaa.
  const withAlpha = (hexColor, a) => hex(chan(hexColor)) + Math.round(Math.max(0, Math.min(1, a)) * 255).toString(16).padStart(2, "0");
  function lum(c) {
    const l = c.map((v) => v / 255).map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
    return 0.2126 * l[0] + 0.7152 * l[1] + 0.0722 * l[2];
  }
  const ratio = (a, b) => {
    const [x, y] = [lum(a), lum(b)];
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  // `top` at alpha `a` over `under` (straight alpha, sRGB values).
  const over = (top, a, under) => top.map((v, i) => v * a + under[i] * (1 - a));

  // The grey of relative luminance `l` (0..1).
  function grey(l) {
    const v = l <= 0.0031308 ? 12.92 * l : 1.055 * l ** (1 / 2.4) - 0.055;
    const c = Math.round(Math.max(0, Math.min(1, v)) * 255);
    return [c, c, c];
  }
  // A measured backdrop range [lmin, lmax] (relative luminance of the frost), else the
  // full range (black .. white).
  const span = (range) =>
    Array.isArray(range) && range.length === 2 && range.every((v) => Number.isFinite(v) && v >= 0 && v <= 1) && range[0] <= range[1]
      ? range
      : [0, 1];

  // What the ink sits on at tint alpha `a` over the worst backdrop in `range` (luminance
  // [lmin, lmax] of the frost under the panel; black and white without one) and its ratio.
  function worst(tintHex, inkHex, a, range) {
    const t = chan(tintHex);
    const ink = chan(inkHex);
    const [lo, hi] = span(range);
    const cands = [grey(lo), grey(hi)].map((b) => over(t, a, b));
    const r = cands.map((c) => ratio(ink, c));
    const k = r[0] <= r[1] ? 0 : 1;
    return { bg: hex(cands[k]), ratio: r[k] };
  }

  // {alpha, densified, contrast}: the lowest alpha >= `base` at which `inkHex` keeps `min`
  // on `tintHex` over any backdrop.
  function tint(tintHex, inkHex, base, min = MIN_CONTRAST, range = null) {
    for (let a = Math.round(base * 100); a <= 100; a++) {
      const w = worst(tintHex, inkHex, a / 100, range);
      if (w.ratio >= min) return { alpha: a / 100, densified: a / 100 > base + 1e-9, contrast: Math.round(w.ratio * 100) / 100 };
    }
    const w = worst(tintHex, inkHex, 1, range);
    return { alpha: 1, densified: true, contrast: Math.round(w.ratio * 100) / 100 };
  }

  // `fgHex` itself when it keeps `min` against `bgHex`, else the nearest lighter/darker
  // variant of it that does (vibrancy: an accent on glass is lifted, not the glass made
  // opaque).
  function lift(fgHex, bgHex, min) {
    const bg = chan(bgHex);
    const fg = chan(fgHex);
    if (ratio(fg, bg) >= min) return hex(fg);
    const target = ratio([0, 0, 0], bg) >= ratio([255, 255, 255], bg) ? [0, 0, 0] : [255, 255, 255];
    for (let k = 1; k <= 100; k++) {
      const c = fg.map((v, i) => v + (target[i] - v) * (k / 100));
      if (ratio(c, bg) >= min) return hex(c);
    }
    return hex(target);
  }

  // The accent as text on glass at tint alpha `a`: lifted until it keeps 3:1 (large text)
  // over the worst frost in `range`.
  function accentOn(tintHex, accentHex, a, range) {
    let c = hex(chan(accentHex));
    for (let i = 0; i < 3; i++) c = lift(c, worst(tintHex, c, a, range).bg, LARGE_CONTRAST);
    return c;
  }

  // Deterministic integer hash noise in 0..1 (no Math.random: frames must not depend on it).
  function hash(x, y, seed) {
    let h = (x * 374761393 + y * 668265263 + seed * 2147483647) | 0;
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    h ^= h >>> 16;
    return (h >>> 0) / 4294967296;
  }

  // Film grain tile (RGBA bytes): light and dark specks at low alpha; tiled over a glass
  // panel it breaks up banding in the tint and reads as a real material.
  function grain(size = 96, seed = 7, strength = 0.07) {
    const px = new Uint8ClampedArray(size * size * 4);
    for (let y = 0; y < size; y++)
      for (let x = 0; x < size; x++) {
        const n = hash(x, y, seed);
        const v = n > 0.5 ? 255 : 0;
        const i = (y * size + x) * 4;
        px[i] = px[i + 1] = px[i + 2] = v;
        px[i + 3] = Math.round(Math.abs(n - 0.5) * 2 * strength * 255);
      }
    return { width: size, height: size, data: px };
  }

  // A neutral stand-in for video under the brand sheet's tiles (no third-party media):
  // a soft two-tone gradient, a few large out-of-focus discs, fine diagonal stripes and
  // grain. The stripes and discs give the frost something to blur, so glass is visible.
  function backdrop(w, h, seed = 3) {
    const px = new Uint8ClampedArray(w * h * 4);
    const discs = [];
    for (let k = 0; k < 6; k++) {
      const hue = hash(k, 1, seed);
      discs.push({
        x: hash(k, 2, seed) * w,
        y: hash(k, 3, seed) * h,
        r: (0.18 + 0.3 * hash(k, 4, seed)) * Math.min(w, h),
        c: [150 + 90 * Math.cos(6.283 * hue), 150 + 90 * Math.cos(6.283 * (hue + 0.33)), 150 + 90 * Math.cos(6.283 * (hue + 0.67))],
      });
    }
    const stripe = Math.max(6, Math.round(Math.min(w, h) * 0.018));
    for (let y = 0; y < h; y++) {
      const v = y / h;
      const base = [205 - 150 * v, 200 - 140 * v, 190 - 120 * v];
      for (let x = 0; x < w; x++) {
        let c = base.slice();
        for (const d of discs) {
          const q = Math.hypot(x - d.x, y - d.y) / d.r;
          if (q < 1) c = over(d.c, 0.75 * (1 - q * q) ** 2, c);
        }
        const s = Math.floor((x + y) / stripe) % 2 ? 1.05 : 0.95;
        const g = (hash(x, y, seed) - 0.5) * 18;
        const i = (y * w + x) * 4;
        px[i] = c[0] * s + g;
        px[i + 1] = c[1] * s + g;
        px[i + 2] = c[2] * s + g;
        px[i + 3] = 255;
      }
    }
    return { width: w, height: h, data: px };
  }

  // [lmin, lmax] of the frost over the generated backdrop (the 0.5th and 99.5th
  // percentile of relative luminance after a blur like the frost's): the vibrancy guard's
  // range where the backdrop stands in for video (brand sheet).
  function backdropRange(w, h) {
    const sw = 64;
    const sh = Math.max(2, Math.round((sw * h) / w));
    const b = backdrop(sw, sh);
    const r = Math.max(1, Math.round(FROST_SIGMA * Math.min(sw, sh) * 1.7));
    const ls = [];
    for (let y = 0; y < sh; y++)
      for (let x = 0; x < sw; x++) {
        const acc = [0, 0, 0];
        let n = 0;
        for (let dy = -r; dy <= r; dy++)
          for (let dx = -r; dx <= r; dx++) {
            const xx = Math.min(sw - 1, Math.max(0, x + dx));
            const yy = Math.min(sh - 1, Math.max(0, y + dy));
            const i = (yy * sw + xx) * 4;
            acc[0] += b.data[i];
            acc[1] += b.data[i + 1];
            acc[2] += b.data[i + 2];
            n++;
          }
        ls.push(lum(acc.map((v) => v / n)));
      }
    ls.sort((a, b2) => a - b2);
    const at = (p) => Math.round(ls[Math.min(ls.length - 1, Math.floor(p * ls.length))] * 10000) / 10000;
    return [at(0.005), at(0.995)];
  }

  // Frost parameters for a frame with short side `unit` and a dark|light theme (written
  // next to the mask clip; `render` reads them from there).
  function frost(unit, theme) {
    return {
      sigma: Math.round(FROST_SIGMA * unit * 10) / 10,
      saturation: FROST_SATURATION,
      brightness: FROST_BRIGHTNESS[theme === "dark" ? "dark" : "light"],
    };
  }

  return { LARGE_CONTRAST, grey, span, FROST_SIGMA, FROST_SATURATION, FROST_BRIGHTNESS, TINT, MIN_CONTRAST, chan, hex, withAlpha, lum, ratio, over, worst, tint, lift, accentOn, hash, grain, backdrop, backdropRange, frost };
});
