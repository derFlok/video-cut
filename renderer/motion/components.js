// Motion components for scene modules (classic script, runs in the sandboxed page).
//
// Every colour, font, radius, depth and motion energy comes from the brand profile through
// the custom properties and vc.style the runtime resolves (--vc-panel, --vc-ink, --vc-hi,
// --vc-shadow, ...; see runtime.js). Sizes are shares of the unit (the frame's short side);
// type sizes are also multiplied by the brand's type_scale, durations by its energy.
//
// Contract of every component: `c.name(parent, props)` builds its DOM under `parent`, puts
// its tweens on vc.tl at the given scene times, emits cues (vc.cue) for the sound track and
// returns its root element. Common props:
//   at     when it enters (s); most components derive it from their first item
//   out    when it leaves (s, optional; blur-out)
//   after  an element on screen that MORPHS into this one at `at` (hand-over instead of
//          a fade: the old element flies into the new one's place and dissolves into it)
//   micro  false switches the micro-events off (default on: while a component is on
//          screen and nothing else happens, an item pulses or a sheen passes every
//          vc.style.beat seconds, so the band never stands still)
// renderer/motion/CATALOGUE.md documents each component (props, cues, durations).
window.__vcComponents = function (vc) {
  "use strict";
  const A = vc.anime;
  const U = vc.unit;
  const S = vc.style;
  const TS = S.type_scale;
  const ms = vc.ms;
  const px = (k) => `${Math.round(k * U * 10) / 10}px`;
  const fz = (k) => `${Math.round(k * U * TS * 10) / 10}px`;
  const D = (msec) => Math.round(msec * S.dur);
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  vc.css(`
    .vc-card { position: relative; background: linear-gradient(170deg, var(--vc-panel-2), var(--vc-panel) 62%);
      color: var(--vc-ink); border-radius: var(--vc-radius); padding: ${px(0.036)} ${px(0.044)};
      box-shadow: var(--vc-shadow), inset 0 ${px(0.0015)} 0 var(--vc-ink-a10); }
    .vc-card.lg { box-shadow: var(--vc-shadow-lg), inset 0 ${px(0.0015)} 0 var(--vc-ink-a10); }
    .vc-fx { position: absolute; inset: 0; border-radius: inherit; overflow: hidden; pointer-events: none; z-index: 3; }
    .vc-sheen { position: absolute; top: -20%; bottom: -20%; width: 38%; left: 0; opacity: 0;
      background: linear-gradient(100deg, var(--vc-ink-a0) 10%, var(--vc-sheen) 50%, var(--vc-ink-a0) 90%); filter: blur(${px(0.012)}); }
    .vc-slot { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center;
      justify-content: center; gap: ${px(0.02)}; }
    .vc-label { font-weight: var(--vc-w-mid); color: var(--vc-ink-muted); font-size: ${fz(0.034)}; letter-spacing: 0.01em;
      white-space: nowrap; line-height: 1.2; }
    .vc-kicker { display: flex; align-items: center; justify-content: space-between; gap: ${px(0.03)}; margin-bottom: ${px(0.016)}; }
    .vc-pill { display: inline-flex; align-items: center; gap: ${px(0.008)}; padding: ${px(0.006)} ${px(0.016)};
      border-radius: 999px; background: var(--vc-hi-soft); color: var(--vc-hi-text); font-weight: var(--vc-w-strong);
      font-size: ${fz(0.03)}; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .vc-pill.solid { background: var(--vc-hi); color: var(--vc-on-hi); box-shadow: 0 0 ${px(0.02)} var(--vc-glow); }
    .vc-num { font-weight: var(--vc-w-strong); font-variant-numeric: tabular-nums; line-height: 0.95; letter-spacing: -0.035em;
      text-shadow: var(--vc-text-glow); }
    .vc-ico { display: block; flex: none; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; overflow: visible; }

    .vc-head { display: flex; flex-wrap: wrap; justify-content: center; align-items: baseline;
      gap: ${px(0.004)} ${px(0.02)}; font-weight: var(--vc-w-strong); line-height: 1.04; text-align: center; letter-spacing: -0.02em; }
    .vc-head.vc-card { padding: ${px(0.026)} ${px(0.044)}; width: fit-content; max-width: 100%; }
    .vc-head .w { position: relative; display: inline-block; padding: 0 ${px(0.01)}; white-space: nowrap; }
    .vc-head .w > .t { position: relative; z-index: 1; }
    .vc-head .mark { position: absolute; left: 0; right: 0; transform-origin: 0 50%; z-index: 0; }
    .vc-head .mark.box { top: 6%; bottom: 0; background: var(--vc-hi); border-radius: var(--vc-radius-sm); box-shadow: 0 0 ${px(0.03)} var(--vc-glow); }
    .vc-head .mark.underline { height: 0.11em; bottom: -0.02em; background: var(--vc-hi); border-radius: 0.06em; box-shadow: 0 0 ${px(0.02)} var(--vc-glow); }
    .vc-head .w.glow > .t { color: var(--vc-hi-text); text-shadow: var(--vc-text-glow); }

    .vc-stat { display: flex; flex-direction: column; align-items: center; text-align: center; padding: ${px(0.03)} ${px(0.05)}; }
    .vc-stat .row { display: flex; align-items: baseline; gap: ${px(0.02)}; }
    .vc-stat .unit { font-weight: var(--vc-w-strong); color: var(--vc-hi-text); line-height: 1; position: relative; display: inline-grid; }
    .vc-stat .unit > span { grid-area: 1 / 1; white-space: nowrap; }
    .vc-stat .src { margin-top: ${px(0.012)}; font-size: ${fz(0.024)}; color: var(--vc-ink-muted); font-weight: var(--vc-w-regular); white-space: nowrap; }
    .vc-stat .bar { margin-top: ${px(0.014)}; height: ${px(0.007)}; width: 100%; border-radius: 9px; background: var(--vc-line); overflow: hidden; }
    .vc-stat .bar > i { display: block; height: 100%; width: 100%; background: var(--vc-hi); transform-origin: 0 50%; box-shadow: 0 0 ${px(0.02)} var(--vc-glow); }
    .vc-digits { display: inline-flex; }
    .vc-digits .col { height: 1.1em; overflow: hidden; position: relative; }
    .vc-digits .reel { display: flex; flex-direction: column; }
    .vc-digits .reel > span { height: 1.1em; line-height: 1.1em; display: block; text-align: center; }

    .vc-list .rows { display: grid; column-gap: ${px(0.05)}; }
    .vc-list .row { display: flex; align-items: center; gap: ${px(0.022)}; padding: ${px(0.01)} 0; position: relative; }
    .vc-list .box { flex: none; width: 1.1em; height: 1.1em; border-radius: var(--vc-radius-sm); position: relative;
      border: ${px(0.004)} solid var(--vc-line); }
    .vc-list .fill { position: absolute; inset: ${px(-0.004)}; border-radius: inherit; background: var(--vc-hi); transform: scale(0);
      box-shadow: 0 0 ${px(0.022)} var(--vc-glow); }
    .vc-list svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
    .vc-list path { fill: none; stroke: var(--vc-on-hi); stroke-width: 3.4; stroke-linecap: round; stroke-linejoin: round; }
    .vc-list .txt { font-weight: var(--vc-w-strong); line-height: 1.1; white-space: nowrap; letter-spacing: -0.01em; }

    .vc-strike { position: relative; display: inline-block; white-space: nowrap; }
    .vc-strike .line { position: absolute; left: -3%; right: -3%; top: 52%; height: 0.1em; background: var(--vc-hi);
      border-radius: 0.06em; transform-origin: 0 50%; box-shadow: 0 0 ${px(0.016)} var(--vc-glow); }

    .vc-win { padding: 0; overflow: hidden; display: flex; flex-direction: column; }
    .vc-win .bar { display: flex; align-items: center; gap: ${px(0.011)}; padding: ${px(0.016)} ${px(0.022)};
      background: var(--vc-panel-2); border-bottom: ${px(0.0015)} solid var(--vc-line); }
    .vc-win .bar i { width: ${px(0.016)}; height: ${px(0.016)}; border-radius: 50%; background: var(--vc-line); flex: none; }
    .vc-win .bar i:first-child { background: var(--vc-hi); box-shadow: 0 0 ${px(0.01)} var(--vc-glow); }
    .vc-win .url { flex: 1; margin-left: ${px(0.016)}; height: ${px(0.034)}; border-radius: 999px; background: var(--vc-panel);
      display: flex; align-items: center; padding: 0 ${px(0.016)}; gap: ${px(0.008)}; font-size: ${fz(0.022)};
      color: var(--vc-ink-muted); white-space: nowrap; overflow: hidden; }
    .vc-win .ttl { flex: 1; text-align: center; font-size: ${fz(0.022)}; color: var(--vc-ink-muted); font-weight: var(--vc-w-mid); margin-right: ${px(0.06)}; }
    .vc-win .body { position: relative; padding: ${px(0.022)} ${px(0.028)}; flex: 1; }
    .vc-win.phone { border-radius: calc(var(--vc-radius) * 2.2); padding: ${px(0.014)}; background: var(--vc-panel-2); }
    .vc-win.phone .screen { border-radius: calc(var(--vc-radius) * 1.7); background: var(--vc-panel); overflow: hidden; display: flex; flex-direction: column; flex: 1; }
    .vc-win.phone .status { height: ${px(0.04)}; display: flex; justify-content: center; align-items: center; }
    .vc-win.phone .status i { width: 28%; height: ${px(0.016)}; border-radius: 999px; background: var(--vc-panel-2); }

    .vc-term .body { font-size: ${fz(0.03)}; line-height: 1.45; font-variant-numeric: tabular-nums; white-space: pre-wrap; letter-spacing: 0; }
    .vc-term .p { color: var(--vc-hi-text); font-weight: var(--vc-w-strong); }
    .vc-term .out { color: var(--vc-ink-muted); }
    .vc-term .caret { display: inline-block; width: 0.55em; height: 1.05em; vertical-align: -0.18em; background: var(--vc-hi);
      margin-left: 0.04em; box-shadow: 0 0 ${px(0.012)} var(--vc-glow); }

    .vc-chart { display: flex; flex-direction: column; }
    .vc-chart .plot { position: relative; display: flex; align-items: flex-end; gap: ${px(0.022)}; }
    .vc-chart .bcol { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 100%; gap: ${px(0.008)}; }
    .vc-chart .bval { font-weight: var(--vc-w-strong); font-size: ${fz(0.034)}; font-variant-numeric: tabular-nums; white-space: nowrap; }
    .vc-chart .bar { width: 100%; border-radius: var(--vc-radius-sm) var(--vc-radius-sm) ${px(0.004)} ${px(0.004)};
      background: linear-gradient(180deg, var(--vc-ink-muted), var(--vc-line)); transform-origin: 50% 100%; }
    .vc-chart .bcol.hi .bar { background: linear-gradient(180deg, var(--vc-hi), var(--vc-hi-soft)); box-shadow: 0 0 ${px(0.03)} var(--vc-glow); }
    .vc-chart .bcol.hi .bval { color: var(--vc-hi-text); text-shadow: var(--vc-text-glow); }
    .vc-chart .blab { font-size: ${fz(0.024)}; color: var(--vc-ink-muted); font-weight: var(--vc-w-mid); white-space: nowrap; }
    .vc-chart .base { position: absolute; left: 0; right: 0; bottom: 0; height: ${px(0.002)}; background: var(--vc-line); }
    .vc-chart svg { overflow: visible; display: block; }
    .vc-chart .ln { fill: none; stroke: var(--vc-hi); stroke-linecap: round; stroke-linejoin: round; }
    .vc-chart .grid { stroke: var(--vc-line); }
    .vc-chart .dot { fill: var(--vc-hi); }
    .vc-chart .ring { fill: none; stroke: var(--vc-hi); }
    .vc-chart .tag { position: absolute; transform: translate(-50%, -135%); }

    .vc-cmp { display: flex; gap: ${px(0.022)}; align-items: stretch; }
    .vc-cmp .side { flex: 1; display: flex; flex-direction: column; gap: ${px(0.012)}; min-width: 0; position: relative; }
    .vc-cmp .ttl { font-weight: var(--vc-w-strong); font-size: ${fz(0.046)}; letter-spacing: -0.02em; white-space: nowrap; }
    .vc-cmp .it { display: flex; gap: ${px(0.012)}; align-items: center; font-size: ${fz(0.03)}; color: var(--vc-ink-muted); white-space: nowrap; }
    .vc-cmp .big { font-size: ${fz(0.09)}; }
    .vc-cmp .badge { position: absolute; top: ${px(-0.02)}; right: ${px(-0.02)}; width: ${px(0.07)}; height: ${px(0.07)};
      border-radius: 50%; background: var(--vc-hi); color: var(--vc-on-hi); display: flex; align-items: center; justify-content: center;
      box-shadow: 0 0 ${px(0.03)} var(--vc-glow); }
    .vc-cmp .vs { align-self: center; font-weight: var(--vc-w-strong); color: var(--vc-ink-muted); font-size: ${fz(0.03)}; }

    .vc-steps { display: flex; align-items: flex-start; position: relative; }
    .vc-steps .st { flex: 1; display: flex; flex-direction: column; align-items: center; gap: ${px(0.012)}; position: relative; z-index: 1; min-width: 0; }
    .vc-steps .node { width: ${px(0.07)}; height: ${px(0.07)}; border-radius: 50%; background: var(--vc-panel);
      border: ${px(0.004)} solid var(--vc-line); display: flex; align-items: center; justify-content: center;
      font-weight: var(--vc-w-strong); font-size: ${fz(0.03)}; color: var(--vc-ink-muted); position: relative; }
    .vc-steps .node .on { position: absolute; inset: ${px(-0.004)}; border-radius: 50%; background: var(--vc-hi); color: var(--vc-on-hi);
      display: flex; align-items: center; justify-content: center; transform: scale(0); box-shadow: 0 0 ${px(0.03)} var(--vc-glow); }
    .vc-steps .t1 { font-weight: var(--vc-w-strong); font-size: ${fz(0.032)}; text-align: center; white-space: nowrap; }
    .vc-steps .t2 { font-size: ${fz(0.022)}; color: var(--vc-ink-muted); text-align: center; white-space: nowrap; }
    .vc-steps .track { position: absolute; height: ${px(0.005)}; background: var(--vc-line); border-radius: 9px; z-index: 0; }
    .vc-steps .track > i { display: block; height: 100%; background: var(--vc-hi); transform-origin: 0 50%; transform: scaleX(0);
      border-radius: inherit; box-shadow: 0 0 ${px(0.016)} var(--vc-glow); }

    .vc-table { padding: ${px(0.02)} ${px(0.026)}; }
    .vc-table table { border-collapse: separate; border-spacing: 0; width: 100%; font-size: ${fz(0.03)}; }
    .vc-table th { text-align: left; color: var(--vc-ink-muted); font-weight: var(--vc-w-mid); padding: ${px(0.008)} ${px(0.014)};
      font-size: ${fz(0.024)}; border-bottom: ${px(0.0015)} solid var(--vc-line); white-space: nowrap; }
    .vc-table td { padding: ${px(0.011)} ${px(0.014)}; font-weight: var(--vc-w-mid); white-space: nowrap; font-variant-numeric: tabular-nums;
      border-bottom: ${px(0.0015)} solid var(--vc-line); }
    .vc-table td.num, .vc-table th.num { text-align: right; }
    .vc-table tr.hi td { color: var(--vc-hi-text); font-weight: var(--vc-w-strong); }
    .vc-table tr { position: relative; }

    .vc-chat { display: flex; flex-direction: column; gap: ${px(0.012)}; }
    .vc-chat .msg { max-width: 82%; padding: ${px(0.016)} ${px(0.024)}; border-radius: calc(var(--vc-radius) * 1.1);
      font-size: ${fz(0.03)}; line-height: 1.3; font-weight: var(--vc-w-mid); background: var(--vc-panel-2); color: var(--vc-ink);
      border-bottom-left-radius: ${px(0.006)}; align-self: flex-start; box-shadow: var(--vc-shadow); }
    .vc-chat .msg.out { align-self: flex-end; background: var(--vc-hi); color: var(--vc-on-hi); border-bottom-left-radius: calc(var(--vc-radius) * 1.1);
      border-bottom-right-radius: ${px(0.006)}; box-shadow: 0 0 ${px(0.025)} var(--vc-glow); }
    .vc-chat .dots { display: inline-flex; gap: ${px(0.008)}; padding: ${px(0.004)} 0; }
    .vc-chat .dots i { width: ${px(0.013)}; height: ${px(0.013)}; border-radius: 50%; background: currentColor; opacity: 0.5; }

    .vc-files .f { display: flex; align-items: center; gap: ${px(0.014)}; padding: ${px(0.007)} ${px(0.012)}; font-size: ${fz(0.027)};
      border-radius: var(--vc-radius-sm); white-space: nowrap; position: relative; }
    .vc-files .f .nm { flex: 1; font-weight: var(--vc-w-mid); }
    .vc-files .f .mt { color: var(--vc-ink-muted); font-size: ${fz(0.022)}; font-variant-numeric: tabular-nums; }
    .vc-files .f .vc-ico { color: var(--vc-ink-muted); }
    .vc-files .f.hi { background: var(--vc-hi-soft); }
    .vc-files .f.hi .vc-ico, .vc-files .f.hi .nm { color: var(--vc-hi-text); }

    .vc-app { display: flex; align-items: center; gap: ${px(0.028)}; }
    .vc-app .tile { width: ${px(0.12)}; height: ${px(0.12)}; border-radius: calc(var(--vc-radius) * 1.1); flex: none;
      background: linear-gradient(150deg, var(--vc-hi), var(--vc-hi-soft)); color: var(--vc-on-hi); display: flex;
      align-items: center; justify-content: center; box-shadow: 0 0 ${px(0.04)} var(--vc-glow); }
    .vc-app .tx { flex: 1; }
    .vc-app .t1 { font-weight: var(--vc-w-strong); font-size: ${fz(0.048)}; letter-spacing: -0.02em; white-space: nowrap; }
    .vc-app .t2 { color: var(--vc-ink-muted); font-size: ${fz(0.028)}; white-space: nowrap; display: flex; gap: ${px(0.006)}; align-items: center; }
    .vc-app .btn { padding: ${px(0.016)} ${px(0.03)}; border-radius: 999px; background: var(--vc-hi); color: var(--vc-on-hi);
      font-weight: var(--vc-w-strong); font-size: ${fz(0.03)}; white-space: nowrap; box-shadow: 0 0 ${px(0.025)} var(--vc-glow); position: relative; }
    .vc-app .stars { display: flex; gap: ${px(0.004)}; }
    .vc-app .stars .vc-ico { color: var(--vc-line); }
    .vc-app .stars .vc-ico.on { color: var(--vc-hi-text); fill: currentColor; }

    .vc-meter .track { height: ${px(0.026)}; border-radius: 999px; background: var(--vc-line); overflow: hidden; margin-top: ${px(0.014)}; }
    .vc-meter .track > i { display: block; height: 100%; width: 100%; border-radius: inherit; transform-origin: 0 50%;
      background: linear-gradient(90deg, var(--vc-hi-soft), var(--vc-hi)); box-shadow: 0 0 ${px(0.03)} var(--vc-glow); }
    .vc-meter .top { display: flex; justify-content: space-between; align-items: baseline; gap: ${px(0.04)}; }
    .vc-meter svg { display: block; overflow: visible; }
    .vc-meter .rbg { fill: none; stroke: var(--vc-line); }
    .vc-meter .rfg { fill: none; stroke: var(--vc-hi); stroke-linecap: round; filter: drop-shadow(0 0 ${px(0.012)} var(--vc-glow)); }

    .vc-chips { display: flex; flex-wrap: wrap; justify-content: center; gap: ${px(0.016)}; max-width: 100%; }
    .vc-chip { display: inline-flex; align-items: center; gap: ${px(0.012)}; padding: ${px(0.016)} ${px(0.028)}; border-radius: 999px;
      background: var(--vc-panel); color: var(--vc-ink); font-weight: var(--vc-w-strong); font-size: ${fz(0.05)}; white-space: nowrap;
      box-shadow: var(--vc-shadow); position: relative; letter-spacing: -0.01em; }
    .vc-chip .vc-ico { color: var(--vc-hi-text); }
    .vc-chip .ring { position: absolute; inset: 0; border-radius: inherit; box-shadow: 0 0 0 ${px(0.004)} var(--vc-hi), 0 0 ${px(0.03)} var(--vc-glow); opacity: 0; }

    .vc-quote { max-width: 100%; }
    .vc-quote .qm { color: var(--vc-hi-text); height: ${px(0.07)}; width: ${px(0.09)}; margin-bottom: ${px(0.006)}; filter: drop-shadow(0 0 ${px(0.014)} var(--vc-glow)); }
    .vc-quote .qt { font-weight: var(--vc-w-strong); font-size: ${fz(0.056)}; line-height: 1.12; letter-spacing: -0.02em; }
    .vc-quote .qt span { display: inline; }
    .vc-quote .by { margin-top: ${px(0.016)}; display: flex; align-items: center; gap: ${px(0.012)}; color: var(--vc-ink-muted); font-size: ${fz(0.028)}; font-weight: var(--vc-w-mid); }
    .vc-quote .by i { width: ${px(0.04)}; height: ${px(0.004)}; background: var(--vc-hi); border-radius: 9px; }

    .vc-ptr { display: flex; flex-direction: column; align-items: center; gap: ${px(0.012)}; }
    .vc-ptr .arw { color: var(--vc-hi-text); filter: drop-shadow(0 0 ${px(0.016)} var(--vc-glow)); }

    .vc-img { padding: ${px(0.012)}; overflow: visible; }
    .vc-img .win { position: relative; overflow: hidden; border-radius: calc(var(--vc-radius) * 0.7); }
    .vc-img img { position: absolute; left: -8%; top: -8%; width: 116%; height: 116%; object-fit: cover; display: block; }
    .vc-img .cap { position: absolute; left: ${px(0.03)}; bottom: ${px(-0.03)}; z-index: 4; }

    .vc-3d { position: absolute; inset: 0; }
    .vc-3d canvas { width: 100%; height: 100%; display: block; }
    .vc-tilt { perspective: ${px(1.4)}; perspective-origin: 50% 40%; display: flex; align-items: center; justify-content: center; }
    .vc-tilt > .plane { transform-style: preserve-3d; }
  `);

  // --- icons ------------------------------------------------------------------------
  // A small generic line-icon set drawn for this project (Apache-2.0, no third-party
  // artwork): 24x24, stroke = currentColor, so it takes the brand's colours.
  const ICONS = {
    check: "M5 12.5l4.5 4.5L19 7.5",
    x: "M6 6l12 12M18 6L6 18",
    plus: "M12 5v14M5 12h14",
    minus: "M5 12h14",
    "arrow-right": "M4 12h15M13 6l6 6-6 6",
    "arrow-down": "M12 4v15M6 13l6 6 6-6",
    "arrow-up": "M12 20V5M6 11l6-6 6 6",
    "trend-up": "M3 17l6-6 4 4 8-8M15 7h6v6",
    "trend-down": "M3 7l6 6 4-4 8 8M15 17h6v-6",
    star: "M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.8z",
    heart: "M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.4a4.3 4.3 0 0 1 7.5 2.4C19.5 15.4 12 20 12 20z",
    bolt: "M13 3L5 13.5h6L10 21l8-10.5h-6z",
    clock: "M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17zM12 7.5V12l3 2",
    calendar: "M4.5 6.5h15v13h-15zM4.5 10.5h15M8.5 4v4M15.5 4v4",
    user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20.5a7.5 7.5 0 0 1 15 0",
    users: "M9 11.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20a6.5 6.5 0 0 1 13 0M16 4.8a3.5 3.5 0 0 1 0 6.4M18 14.2a6.5 6.5 0 0 1 3.5 5.8",
    home: "M4 11l8-6.5 8 6.5M6 9.5V20h12V9.5M10 20v-5.5h4V20",
    pin: "M12 21s-6.5-6.2-6.5-11a6.5 6.5 0 0 1 13 0c0 4.8-6.5 11-6.5 11zM12 12.3a2.3 2.3 0 1 0 0-4.6 2.3 2.3 0 0 0 0 4.6z",
    globe: "M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17zM3.5 12h17M12 3.5c2.5 2.6 3.6 5.4 3.6 8.5s-1.1 5.9-3.6 8.5c-2.5-2.6-3.6-5.4-3.6-8.5s1.1-5.9 3.6-8.5z",
    sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6",
    wave: "M2.5 9c2.4 0 2.4-2 4.75-2S9.6 9 12 9s2.4-2 4.75-2S19.1 9 21.5 9M2.5 15c2.4 0 2.4-2 4.75-2S9.6 15 12 15s2.4-2 4.75-2 2.35 2 4.75 2",
    mountain: "M2.5 19.5L9 8l4 7 2.5-4 6 8.5zM7 11.6l2 1.4 2-1.5",
    bike: "M6 18.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM18 18.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM6 15l4-7h5l3 7M8.5 8h3M15 8l-1.5-3h-2",
    flag: "M5.5 21V4M5.5 4.5h11l-2.2 4 2.2 4h-11",
    paddle: "M5 19l9-9M14 10l2.2-5.2a2.2 2.2 0 0 1 3.1 3.1L14 10zM4 20l1-1",
    file: "M6 3.5h8l4 4v13H6zM14 3.5v4h4",
    folder: "M3.5 6.5h6l2 2h9v10h-17z",
    chart: "M4 20V4M4 20h16M8 16v-4M12 16V8M16 16v-6",
    search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM15.3 15.3L20 20",
    gear: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7",
    chat: "M4 5.5h16v10.5H9.5L5 20v-4H4z",
    lock: "M6 10.5h12v10H6zM8.5 10.5V7.5a3.5 3.5 0 0 1 7 0v3",
    play: "M8 5.5v13l10.5-6.5z",
    euro: "M17.5 6.5A6.5 6.5 0 1 0 17.5 17.5M4.5 10.5h9M4.5 13.5h9",
    percent: "M18.5 5.5L5.5 18.5M7.5 9.5a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM16.5 18.5a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
    spark: "M12 3v5M12 16v5M3 12h5M16 12h5M6 6l3 3M15 15l3 3M6 18l3-3M15 9l3-3",
    quote: "M4 17.5c0-5 1.6-8.5 6-10.5M4 17.5a3 3 0 1 0 6 0 3 3 0 0 0-6 0M14 17.5c0-5 1.6-8.5 6-10.5M14 17.5a3 3 0 1 0 6 0 3 3 0 0 0-6 0",
  };
  function icon(name, parent, size = 0.04, opt = {}) {
    if (!ICONS[name]) throw new Error(`icon "${name}" is not in the set (${Object.keys(ICONS).join(", ")})`);
    const svg = vc.el("svg", "vc-ico", parent);
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.style.width = svg.style.height = fz(size);
    if (opt.stroke) svg.style.strokeWidth = String(opt.stroke);
    const p = vc.el("path", "", svg);
    p.setAttribute("d", ICONS[name]);
    if (opt.draw) {
      p.setAttribute("pathLength", "1");
      p.style.strokeDasharray = "1";
      p.style.strokeDashoffset = "1";
    }
    return svg;
  }

  // --- motion vocabulary --------------------------------------------------------------

  const hide = (el) => {
    el.style.opacity = "0";
    return el;
  };

  // A style value that switches at scene time `at` (anime.js tweens numbers, not tokens).
  function setAt(el, prop, value, at) {
    vc.onSeek((t) => (el.style[prop] = t >= at ? value : ""));
  }

  // Box of `el` relative to the stage, ignoring transforms (layout only).
  function box(el) {
    let x = 0;
    let y = 0;
    let e = el;
    while (e && e !== vc.stage) {
      x += e.offsetLeft;
      y += e.offsetTop;
      e = e.offsetParent;
    }
    return { x, y, w: el.offsetWidth, h: el.offsetHeight, cx: x + el.offsetWidth / 2, cy: y + el.offsetHeight / 2 };
  }

  // Enter: blur + scale + rise into place on the brand spring (overshoot from energy).
  // dir: "up" (default) | "down" | "left" | "right" | "none".
  function pop(el, t, opt = {}) {
    hide(el);
    el.__vcAt = t; // entry time: micro-events only touch what is already on screen
    const d = opt.dir || "up";
    const r = (opt.rise == null ? 0.035 : opt.rise) * U;
    const from = { up: [0, r], down: [0, -r], left: [r, 0], right: [-r, 0], none: [0, 0] }[d] || [0, r];
    const props = {
      opacity: { from: 0, to: 1, ease: "out(3)", duration: D(220) },
      scale: { from: opt.scale == null ? 0.82 : opt.scale, to: 1, ease: opt.soft ? vc.spring : vc.springPop },
      filter: { from: `blur(${(opt.blur == null ? 0.022 : opt.blur) * U}px)`, to: "blur(0px)", ease: "out(3)", duration: D(300) },
    };
    if (from[0]) props.x = { from: from[0], to: 0, ease: vc.spring };
    if (from[1]) props.y = { from: from[1], to: 0, ease: vc.spring };
    vc.tl.add(el, props, ms(t));
    if (opt.cue !== false) vc.cue(opt.cue || "pop", t, { component: opt.component, strength: opt.strength == null ? 0.5 : opt.strength });
    return el;
  }

  // Leave: blur out, shrink and lift, faster than the entrance.
  function leave(el, t, opt = {}) {
    vc.tl.add(
      el,
      {
        opacity: { to: 0, ease: "in(2)", duration: D(230) },
        scale: { to: opt.scale || 0.9, ease: "in(2)", duration: D(260) },
        y: { to: (opt.rise == null ? -0.025 : -opt.rise) * U, ease: "in(2)", duration: D(260) },
        filter: { to: `blur(${0.016 * U}px)`, ease: "in(2)", duration: D(260) },
      },
      ms(t),
    );
    if (opt.cue !== false) vc.cue("swoosh_out", t, { component: opt.component, strength: 0.35 });
    return el;
  }

  // Hand-over: `a` flies into `b`'s place and dissolves while `b` grows out of `a`'s box.
  // Used by every component's `after` prop; call it directly for custom elements.
  function morph(a, b, t, opt = {}) {
    const ba = box(a);
    const bb = box(b);
    const dx = bb.cx - ba.cx;
    const dy = bb.cy - ba.cy;
    const k = clamp(Math.sqrt((bb.w * bb.h) / Math.max(1, ba.w * ba.h)), 0.35, 2.8);
    vc.tl.add(
      a,
      {
        x: { to: dx * 0.7, ease: "inOut(3)", duration: D(380) },
        y: { to: dy * 0.7, ease: "inOut(3)", duration: D(380) },
        scale: { to: k * 0.85, ease: "inOut(3)", duration: D(380) },
        opacity: { to: 0, ease: "in(2)", duration: D(300), delay: D(60) },
        filter: { to: `blur(${0.014 * U}px)`, ease: "in(2)", duration: D(320) },
      },
      ms(t),
    );
    hide(b);
    vc.tl.add(
      b,
      {
        x: { from: -dx * 0.6, to: 0, ease: vc.spring },
        y: { from: -dy * 0.6, to: 0, ease: vc.spring },
        scale: { from: clamp(1 / k, 0.35, 2.8) * 0.9, to: 1, ease: vc.springPop },
        opacity: { from: 0, to: 1, ease: "out(2)", duration: D(260), delay: D(80) },
        filter: { from: `blur(${0.018 * U}px)`, to: "blur(0px)", ease: "out(3)", duration: D(360) },
      },
      ms(t),
    );
    if (opt.cue !== false) vc.cue("whoosh", t, { component: opt.component || "morph", strength: 0.55 });
  }

  // Emphasis: a quick scale bump on the pop spring (and a cue if asked).
  function pulse(el, t, k = 0.06, opt = {}) {
    vc.tl.add(el, { scale: [{ to: 1 + k * S.overshoot, duration: D(130), ease: "out(2)" }, { to: 1, ease: vc.springPop }] }, ms(t));
    if (opt.cue) vc.cue(opt.cue, t, { component: opt.component, strength: opt.strength == null ? 0.3 : opt.strength });
    return el;
  }

  // Nudge sideways (a "yes, this one" wiggle) without scale.
  function nudge(el, t, k = 0.012) {
    vc.tl.add(el, { x: [{ to: k * U, duration: D(120), ease: "out(2)" }, { to: 0, ease: vc.springPop }] }, ms(t));
  }

  // Continuous slow motion while something is on screen (nothing ever stands still).
  function drift(el, t0, t1, amount = 0.02) {
    const a = amount * S.drift;
    if (t1 > t0) vc.tl.add(el, { scale: { from: 1, to: 1 + a }, ease: "linear", duration: ms(t1 - t0), composition: "blend" }, ms(t0));
    return el;
  }

  // A light sheen sweeping over a card (the card needs an .vc-fx layer: see fx()).
  function fx(root) {
    let layer = root.querySelector(":scope > .vc-fx");
    if (!layer) {
      layer = vc.el("div", "vc-fx", root);
      vc.el("div", "vc-sheen", layer);
    }
    return layer;
  }
  function sheen(root, t) {
    const s = fx(root).firstChild;
    vc.tl.add(s, { x: { from: -0.5 * root.offsetWidth, to: 1.6 * root.offsetWidth }, opacity: [{ from: 0, to: 1, duration: D(120) }, { to: 0, duration: D(200), delay: D(260) }], ease: "inOut(2)", duration: D(620) }, ms(t));
  }

  // A micro-event on one element: a pop-spring bump with a small lift (visible on any size).
  function bump(el, t) {
    vc.tl.add(
      el,
      {
        scale: [{ to: 1 + 0.07 * S.overshoot, duration: D(140), ease: "out(2)" }, { to: 1, ease: vc.springPop }],
        y: [{ to: -0.012 * U, duration: D(140), ease: "out(2)" }, { to: 0, ease: vc.springPop }],
      },
      ms(t),
    );
  }

  // Micro-events: fill every gap longer than one beat between a component's own events with
  // a visible change (cycling bumps over `targets`; on a card every third one is a sheen).
  function micro(root, t0, t1, times, targets = []) {
    const beat = S.beat;
    const isCard = root.classList.contains("vc-card");
    const ev = [t0, ...times.filter((x) => x > t0 && x < t1), t1].sort((a, b) => a - b);
    let k = 0;
    for (let i = 0; i + 1 < ev.length; i++) {
      const gap = ev[i + 1] - ev[i];
      if (gap <= beat * 1.15) continue;
      const n = Math.floor(gap / beat) - 1 || 1;
      const step = gap / (n + 1);
      for (let j = 1; j <= n; j++) {
        const t = ev[i] + step * j;
        if (t1 - t < 0.2) continue;
        const shown = targets.filter((e) => !(e.__vcAt > t - 0.2));
        if (!shown.length || (isCard && k % 3 === 2)) (isCard ? sheen : bump)(root, t);
        else bump(shown[k % shown.length], t);
        k++;
      }
    }
  }

  // Every component: enter (pop, or morph out of `after`), micro-events, drift, leave.
  function life(root, name, p, t, times, targets, opt = {}) {
    if (p.after) morph(p.after, root, t, { component: name });
    else pop(root, t, { component: name, strength: opt.strength, cue: opt.cue, scale: opt.scale, dir: opt.dir });
    const end = p.out != null ? p.out : vc.duration;
    if (p.micro !== false) micro(root, t + 0.3, end, times, targets);
    drift(root, t, end, opt.drift == null ? 0.018 : opt.drift);
    if (p.out != null) leave(root, p.out, { component: name });
    return root;
  }

  // A centred, absolutely placed slot in the stage (components stack inside it). `fit`
  // (default true) scales the slot so its content fills the stage (up to `max`, default
  // 1.8x; never wider than 96 % or taller than 92 %): components are designed at a base
  // size and the band decides how big they show. Put 3D backgrounds in a slot with
  // fit: false (they size themselves to the slot).
  function slot(parent = vc.stage, opt = {}) {
    const s = vc.el("div", "vc-slot", parent);
    if (opt.row) s.style.flexDirection = "row";
    if (opt.justify) s.style.justifyContent = opt.justify;
    if (opt.align) s.style.alignItems = opt.align;
    if (opt.gap != null) s.style.gap = px(opt.gap);
    if (opt.fit !== false) {
      let done = false;
      vc.onSeek(() => {
        if (done) return;
        done = true;
        const kids = [...s.children].filter((k) => !k.classList.contains("vc-3d"));
        if (!kids.length) return;
        const W = s.offsetWidth;
        const H = s.offsetHeight;
        const top = Math.min(...kids.map((k) => k.offsetTop));
        const bottom = Math.max(...kids.map((k) => k.offsetTop + k.offsetHeight));
        const left = Math.min(...kids.map((k) => k.offsetLeft));
        const right = Math.max(...kids.map((k) => k.offsetLeft + k.offsetWidth));
        const k = Math.min(opt.max || 1.8, (W * 0.96) / Math.max(1, right - left), (H * 0.92) / Math.max(1, bottom - top));
        s.style.transform = `scale(${k.toFixed(4)})`;
      });
    }
    return s;
  }

  const card = (parent, cls = "", lg = false) => vc.el("div", `vc-card${lg ? " lg" : ""}${cls ? " " + cls : ""}`, parent);
  const first = (...xs) => xs.find((x) => Number.isFinite(x));

  // Odometer digits for an integer (each digit a reel; leading digits appear with the value).
  function digits(parent, { from, to, at, dur, size }) {
    const wrap = vc.el("div", "vc-digits vc-num", parent);
    wrap.style.fontSize = fz(size);
    const n = Math.max(String(Math.abs(Math.round(to))).length, String(Math.abs(Math.round(from))).length);
    const cols = [];
    for (let k = n - 1; k >= 0; k--) {
      const col = vc.el("div", "col", wrap);
      const reel = vc.el("div", "reel", col);
      for (let d = 0; d <= 10; d++) vc.el("span", "", reel, String(d % 10));
      cols.push({ k, reel, col });
    }
    const ease = A.eases.out(4);
    let digitW = 0;
    vc.onSeek((t) => {
      if (!digitW) digitW = Math.max(...cols.map((c) => c.reel.offsetWidth));
      const p = clamp((t - at) / dur, 0, 1);
      const v = from + (to - from) * ease(p);
      for (const { k, reel, col } of cols) {
        const place = 10 ** k;
        const pos = k === 0 ? v % 10 : (Math.floor(v / place) % 10) + Math.max(0, (v % place) - (place - 1));
        reel.style.transform = `translateY(${-pos * 1.1}em)`;
        const on = k === 0 ? 1 : clamp(v - (place - 1), 0, 1);
        col.style.opacity = String(on);
        col.style.width = `${(on * digitW).toFixed(2)}px`;
      }
    });
    return wrap;
  }

  // Number formatting without locale data (deterministic): `sep` groups thousands, `dec`
  // is the decimal mark.
  function fmt(v, decimals = 0, sep = "", dec = ".") {
    const s = Math.abs(v).toFixed(decimals);
    const [i, f] = s.split(".");
    const g = sep ? i.replace(/\B(?=(\d{3})+(?!\d))/g, sep) : i;
    return (v < 0 ? "-" : "") + g + (f ? dec + f : "");
  }

  // Count-up cues: one "count" at the start, ticks while the value runs (at most one per
  // beat), an "impact" when it lands.
  function countCues(name, at, dur, steps) {
    vc.cue("count", at, { component: name, strength: 0.5 });
    const n = Math.min(Math.max(0, Math.floor(steps)), Math.floor(dur / Math.max(0.12, S.beat * 0.5)));
    for (let i = 1; i < n; i++) vc.cue("tick", at + (dur * i) / n, { component: name, strength: 0.2 });
    vc.cue("impact", at + dur, { component: name, strength: 0.7 });
  }

  // --- components --------------------------------------------------------------------

  // Kinetic headline: words build one by one on their times; `mark` boxes, underlines or
  // lights up words. words: [{text, at}], mark: [{index, at, style: "box"|"underline"|"glow"}].
  // Use it for a claim or label the speaker does NOT say word for word (never repeat captions).
  function headline(parent, p = {}) {
    const { words, mark = [], size = 0.085, card: onCard = true } = p;
    const root = vc.el("div", onCard ? "vc-head vc-card" : "vc-head", parent);
    root.style.fontSize = fz(size);
    const spans = words.map((w, i) => {
      const s = vc.el("span", "w", root);
      vc.el("span", "t", s, w.text);
      pop(s, w.at, { scale: 0.6, rise: 0.05, blur: 0.03, component: "headline", strength: 0.2, cue: i === 0 ? false : "pop" });
      return s;
    });
    const times = words.map((w) => w.at);
    for (const m of mark) {
      const s = spans[m.index];
      const st = m.style || "box";
      if (st === "glow") setAt(s, "className", "w glow", m.at);
      else {
        const bar = vc.el("span", `mark ${st}`, s);
        bar.style.transform = "scaleX(0)";
        vc.tl.add(bar, { scaleX: { from: 0, to: 1 }, ease: "out(4)", duration: D(360) }, ms(m.at));
        if (st === "box") setAt(s.firstChild, "color", "var(--vc-on-hi)", m.at + 0.1);
      }
      pulse(s, m.at, 0.12, { cue: "impact", component: "headline", strength: 0.6 });
      times.push(m.at);
    }
    const t = first(p.at, words[0].at - 0.12);
    return life(root, "headline", p, t, times, spans);
  }

  // Big number / stat: a value counting (odometer reels for whole numbers) with a unit, a
  // label and a source line; the unit can change on a word (unitTo at unitAt, e.g. a
  // currency turning into a percentage). Optional `bar` (0..1) fills under the number.
  function stat(parent, p = {}) {
    const { value, from = 0, dur = 0.9, decimals = 0, prefix = "", unit = "", unitTo, unitAt, label, source, size = 0.22, bar, sep = "", dec = "." } = p;
    const at = first(p.countAt, p.at);
    const root = card(parent, "vc-stat", true);
    if (label) {
      const l = vc.el("div", "vc-label", root, label);
      l.style.marginBottom = px(0.004);
    }
    const row = vc.el("div", "row", root);
    if (prefix) {
      const pf = vc.el("div", "unit", row);
      vc.el("span", "", pf, prefix).parentElement.style.fontSize = fz(size * 0.5);
    }
    let num;
    if (decimals === 0 && !sep && value >= 0 && from >= 0) num = digits(row, { from, to: value, at, dur, size });
    else {
      num = vc.el("div", "vc-num", row);
      num.style.fontSize = fz(size);
      const ease = A.eases.out(4);
      vc.onSeek((t) => (num.textContent = fmt(from + (value - from) * ease(clamp((t - at) / dur, 0, 1)), decimals, sep, dec)));
    }
    const times = [at, at + dur];
    if (unit || unitTo) {
      const u = vc.el("div", "unit", row);
      u.style.fontSize = fz(size * 0.42);
      const u1 = vc.el("span", "", u, unit);
      if (unitTo && unitAt != null) {
        const u2 = vc.el("span", "", u, unitTo);
        u2.style.opacity = "0";
        vc.tl.add(u1, { y: { to: -0.5 * size * U }, opacity: { to: 0 }, filter: { to: `blur(${0.01 * U}px)` }, ease: "in(2)", duration: D(220) }, ms(unitAt));
        vc.tl.add(u2, { y: { from: 0.5 * size * U, to: 0 }, opacity: { from: 0, to: 1, duration: D(200) }, ease: vc.springPop }, ms(unitAt + 0.08));
        pulse(num, unitAt, 0.06, { cue: "whoosh", component: "stat", strength: 0.5 });
        times.push(unitAt);
      }
    }
    if (bar != null) {
      const b = vc.el("div", "bar", root);
      const fill = vc.el("i", "", b);
      fill.style.transform = "scaleX(0)";
      vc.tl.add(fill, { scaleX: { from: 0, to: clamp(bar, 0, 1) }, ease: "out(3)", duration: ms(dur) }, ms(at));
    }
    if (source) vc.el("div", "src", root, source);
    countCues("stat", at, dur, Math.abs(value - from));
    pulse(num, at + dur, 0.07);
    const t = first(p.enter, at - 0.25);
    return life(root, "stat", p, t, times, [num]);
  }

  // Spike-compatible counter: a stat without a source line (suffix = unit).
  function counter(parent, p = {}) {
    return stat(parent, { ...p, unit: p.suffix || p.unit, value: p.to, at: p.at, size: p.size || 0.2 });
  }

  // Checklist: items appear on their words and are checked (box fills with glow, the check
  // draws, the row nudges); a tally pill counts 0/n ... n/n. items: [{text, at, check}].
  function checklist(parent, p = {}) {
    const { label, items, size = 0.05, columns = 1, tally = true } = p;
    const root = card(parent, "vc-list");
    let pill = null;
    if (label || tally) {
      const k = vc.el("div", "vc-kicker", root);
      vc.el("div", "vc-label", k, label || "");
      if (tally) pill = vc.el("div", "vc-pill", k, `0/${items.length}`);
    }
    const grid = vc.el("div", "rows", root);
    grid.style.gridTemplateColumns = `repeat(${columns}, auto)`;
    const rows = [];
    const times = [];
    for (const it of items) {
      const row = vc.el("div", "row", grid);
      row.style.fontSize = fz(size);
      const bx = vc.el("div", "box", row);
      const fill = vc.el("div", "fill", bx);
      const svg = vc.el("svg", "", bx);
      svg.setAttribute("viewBox", "0 0 24 24");
      const path = vc.el("path", "", svg);
      path.setAttribute("d", ICONS.check);
      path.setAttribute("pathLength", "1");
      path.style.strokeDasharray = "1";
      path.style.strokeDashoffset = "1";
      vc.el("div", "txt", row, it.text);
      pop(row, it.at, { scale: 0.9, rise: 0.02, dir: "left", component: "checklist", strength: 0.3 });
      times.push(it.at);
      if (it.check != null) {
        vc.tl.add(fill, { scale: { from: 0, to: 1, ease: vc.springPop } }, ms(it.check));
        setAt(bx, "borderColor", "var(--vc-hi)", it.check);
        vc.tl.add(path, { strokeDashoffset: { from: 1, to: 0 }, ease: "out(3)", duration: D(260) }, ms(it.check + 0.06));
        nudge(row, it.check);
        vc.cue("check", it.check, { component: "checklist", strength: 0.6 });
        times.push(it.check);
        if (pill) pulse(pill, it.check + 0.05, 0.12);
      }
      rows.push(row);
    }
    if (pill) {
      const checks = items.map((i) => i.check).filter(Number.isFinite);
      vc.onSeek((t) => {
        const n = checks.filter((c) => t >= c).length;
        pill.textContent = `${n}/${items.length}`;
        pill.className = n === items.length ? "vc-pill solid" : "vc-pill";
      });
    }
    const t = first(p.enter, p.at, items[0].at - 0.2);
    return life(root, "checklist", p, t, times, rows);
  }

  // Strike and swap: `text` is struck on a word and shrinks to muted; `then` (the answer)
  // pops in big and bright. {label, text, at, strikeAt, then, thenAt}.
  function strikeCard(parent, p = {}) {
    const { label, text, strikeAt, then, size = 0.07 } = p;
    const root = card(parent, "", true);
    root.style.textAlign = "center";
    if (label) vc.el("div", "vc-label", root, label);
    const wrap = vc.el("div", "vc-strike", root);
    wrap.style.fontSize = fz(size);
    wrap.style.fontWeight = "var(--vc-w-strong)";
    const word = vc.el("span", "", wrap, text);
    const line = vc.el("span", "line", wrap);
    line.style.transform = "scaleX(0)";
    vc.tl.add(line, { scaleX: { from: 0, to: 1 }, ease: "inOut(3)", duration: D(380) }, ms(strikeAt));
    vc.cue("strike", strikeAt, { component: "strikeCard", strength: 0.7 });
    vc.tl.add(word, { opacity: { from: 1, to: 0.4 }, ease: "linear", duration: D(280) }, ms(strikeAt + 0.2));
    vc.tl.add(wrap, { scale: { to: 0.72 }, ease: "out(3)", duration: D(380) }, ms(strikeAt + 0.25));
    const times = [strikeAt];
    let fix = null;
    if (then) {
      fix = vc.el("div", "vc-num", root, then);
      fix.style.fontSize = fz(size * 1.3);
      fix.style.color = "var(--vc-hi-text)";
      const ta = p.thenAt == null ? strikeAt + 0.45 : p.thenAt;
      pop(fix, ta, { scale: 0.5, component: "strikeCard", cue: "impact", strength: 0.7 });
      times.push(ta);
    }
    return life(root, "strikeCard", p, first(p.at, strikeAt - 0.6), times, fix ? [wrap, fix] : [wrap]);
  }

  // UI frame rebuilt from parts (generic, no real product): kind "browser" (dots + address
  // pill), "window" (dots + title), "phone" (device frame), "terminal" (window, dark-on-panel
  // type). Returns the root; `root.body` is where content goes (chat, fileList, any element).
  function frame(parent, p = {}) {
    const kind = p.kind || "window";
    const root = card(parent, `vc-win ${kind === "phone" ? "phone" : ""}`, true);
    if (p.width) root.style.width = px(p.width);
    if (p.height) root.style.height = px(p.height);
    let body;
    if (kind === "phone") {
      const scr = vc.el("div", "screen", root);
      vc.el("i", "", vc.el("div", "status", scr));
      body = vc.el("div", "body", scr);
    } else {
      const bar = vc.el("div", "bar", root);
      for (let i = 0; i < 3; i++) vc.el("i", "", bar);
      if (kind === "browser") {
        const url = vc.el("div", "url", bar);
        icon("lock", url, 0.02);
        vc.el("span", "", url, p.url || "");
      } else vc.el("div", "ttl", bar, p.title || "");
      body = vc.el("div", "body", root);
    }
    root.body = body;
    if (p.at != null) life(root, "frame", p, p.at, p.times || [], p.targets || [], { strength: 0.55 });
    return root;
  }

  // Terminal: lines typed at `cps` characters per second; `out: true` lines appear at once.
  // lines: [{text, at, cps, prompt, out}]. Each typed line emits a "type" cue.
  function terminal(parent, p = {}) {
    const { lines, width = 0.84 } = p;
    const root = frame(parent, { kind: "terminal", title: p.title || "", width });
    root.classList.add("vc-term");
    const body = root.body;
    const rows = lines.map((l) => {
      const row = vc.el("div", l.out ? "out" : "", body);
      row.style.display = "none";
      const pr = l.out || l.prompt === false ? null : vc.el("span", "p", row, "> ");
      const txt = vc.el("span", "", row);
      if (l.out) vc.cue("tick", l.at, { component: "terminal", strength: 0.3 });
      else vc.cue("type", l.at, { component: "terminal", strength: clamp(l.text.length / 40, 0.2, 0.8) });
      return { l, row, pr, txt };
    });
    const caret = vc.el("span", "caret", null);
    vc.onSeek((t) => {
      let last = null;
      for (const r of rows) {
        const cps = (r.l.cps || 28) / S.dur;
        const shown = r.l.out ? (t >= r.l.at ? r.l.text.length : 0) : clamp(Math.floor((t - r.l.at) * cps), 0, r.l.text.length);
        r.row.style.display = t >= r.l.at ? "" : "none";
        r.txt.textContent = r.l.text.slice(0, shown);
        if (t >= r.l.at) last = r;
      }
      if (last) last.row.appendChild(caret);
      const typing = rows.some((r) => !r.l.out && t >= r.l.at && t < r.l.at + (r.l.text.length * S.dur) / (r.l.cps || 28));
      caret.style.opacity = typing || Math.floor(t * 2.4) % 2 === 0 ? "1" : "0";
    });
    return life(root, "terminal", p, first(p.enter, p.at, lines[0].at - 0.3), lines.map((l) => l.at), rows.map((r) => r.row));
  }

  // Bar chart: bars grow on their words (value labels count up with them); `highlight`
  // (index) lights the winner. bars: [{label, value, at}], unit, max, height.
  function barChart(parent, p = {}) {
    const { bars, unit = "", height = 0.24, title, decimals = 0, width = 0.78 } = p;
    const max = p.max || Math.max(...bars.map((b) => b.value));
    const root = card(parent, "vc-chart", true);
    root.style.width = px(width);
    if (title) {
      const k = vc.el("div", "vc-kicker", root);
      vc.el("div", "vc-label", k, title);
      icon("chart", k, 0.032).style.color = "var(--vc-hi-text)";
    }
    const plot = vc.el("div", "plot", root);
    plot.style.height = px(height);
    vc.el("div", "base", plot);
    const cols = [];
    const times = [];
    bars.forEach((b, i) => {
      const col = vc.el("div", `bcol${i === p.highlight ? " hi" : ""}`, plot);
      const val = vc.el("div", "bval", col);
      const bar = vc.el("div", "bar", col);
      bar.style.height = `${Math.round((b.value / max) * 100 * 0.78)}%`;
      bar.style.transform = "scaleY(0)";
      vc.el("div", "blab", col, b.label || "");
      const dur = 0.55 * S.dur;
      vc.tl.add(bar, { scaleY: { from: 0, to: 1, ease: vc.springPop } }, ms(b.at));
      hide(val);
      vc.tl.add(val, { opacity: { from: 0, to: 1, duration: D(160) }, y: { from: 0.02 * U, to: 0, ease: vc.spring } }, ms(b.at));
      vc.onSeek((t) => (val.textContent = fmt(b.value * A.eases.out(3)(clamp((t - b.at) / dur, 0, 1)), decimals) + unit));
      vc.cue("pop", b.at, { component: "barChart", strength: clamp(0.25 + 0.5 * (b.value / max), 0, 1) });
      times.push(b.at);
      cols.push(col);
    });
    if (p.highlight != null && p.highlightAt != null) {
      pulse(cols[p.highlight], p.highlightAt, 0.08, { cue: "impact", component: "barChart", strength: 0.7 });
      times.push(p.highlightAt);
    }
    return life(root, "barChart", p, first(p.enter, p.at, bars[0].at - 0.3), times, cols);
  }

  // Line / area chart: the line draws from point to point, each point landing on its word;
  // the head glows, the area fills under it, the last value shows in a pill.
  // points: [{v, at, label?}], unit, area (default true).
  function lineChart(parent, p = {}) {
    const { points, unit = "", width = 0.78, height = 0.24, area = true, title, decimals = 0 } = p;
    const root = card(parent, "vc-chart", true);
    root.style.width = px(width);
    if (title) {
      const k = vc.el("div", "vc-kicker", root);
      vc.el("div", "vc-label", k, title);
      icon("trend-up", k, 0.032).style.color = "var(--vc-hi-text)";
    }
    const W = Math.round(width * U - 0.088 * U);
    const H = Math.round(height * U);
    const plot = vc.el("div", "plot", root);
    plot.style.height = `${H}px`;
    plot.style.width = `${W}px`;
    const svg = vc.el("svg", "", plot);
    svg.setAttribute("width", W);
    svg.setAttribute("height", H);
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const lo = Math.min(0, ...points.map((q) => q.v));
    const hi = Math.max(...points.map((q) => q.v));
    const P = points.map((q, i) => [(i / Math.max(1, points.length - 1)) * W, H - ((q.v - lo) / (hi - lo || 1)) * H * 0.86]);
    for (let g = 1; g <= 3; g++) {
      const ln = vc.el("line", "grid", svg);
      ln.setAttribute("x1", 0);
      ln.setAttribute("x2", W);
      ln.setAttribute("y1", (H * g) / 4);
      ln.setAttribute("y2", (H * g) / 4);
      ln.setAttribute("stroke-width", Math.max(1, U * 0.0015));
    }
    const defs = vc.el("defs", "", svg);
    const gid = `vcg${Math.round(vc.random(0, 1e6, 0))}`;
    const grad = vc.el("linearGradient", "", defs);
    grad.setAttribute("id", gid);
    grad.setAttribute("x1", 0);
    grad.setAttribute("x2", 0);
    grad.setAttribute("y1", 0);
    grad.setAttribute("y2", 1);
    const s1 = vc.el("stop", "", grad);
    s1.setAttribute("offset", "0");
    s1.style.stopColor = "var(--vc-hi)";
    s1.style.stopOpacity = "0.45";
    const s2 = vc.el("stop", "", grad);
    s2.setAttribute("offset", "1");
    s2.style.stopColor = "var(--vc-hi)";
    s2.style.stopOpacity = "0";
    const fill = area ? vc.el("path", "", svg) : null;
    if (fill) fill.setAttribute("fill", `url(#${gid})`);
    const line = vc.el("path", "ln", svg);
    line.setAttribute("stroke-width", U * 0.008);
    line.style.filter = `drop-shadow(0 0 ${U * 0.01}px var(--vc-glow))`;
    const ring = vc.el("circle", "ring", svg);
    ring.setAttribute("stroke-width", U * 0.003);
    const dot = vc.el("circle", "dot", svg);
    dot.setAttribute("r", U * 0.012);
    const pill = vc.el("div", "vc-pill solid tag", plot);
    const times = points.map((q) => q.at);
    const ease = A.eases.inOut(2);
    vc.onSeek((t) => {
      // head position: between point i and i+1 while t runs from at_i to at_{i+1}
      let pts = [P[0]];
      let head = P[0];
      let val = points[0].v;
      for (let i = 0; i + 1 < P.length; i++) {
        const a = points[i].at;
        const b = points[i + 1].at;
        if (t <= a) break;
        const k = ease(clamp((t - a) / Math.max(0.05, Math.min(b - a, 0.6 * S.dur)), 0, 1));
        head = [P[i][0] + (P[i + 1][0] - P[i][0]) * k, P[i][1] + (P[i + 1][1] - P[i][1]) * k];
        val = points[i].v + (points[i + 1].v - points[i].v) * k;
        pts.push(head);
        if (k < 1) break;
      }
      const d = pts.map((q, i) => `${i ? "L" : "M"}${q[0].toFixed(1)} ${q[1].toFixed(1)}`).join(" ");
      line.setAttribute("d", d);
      if (fill) fill.setAttribute("d", `${d} L${head[0].toFixed(1)} ${H} L0 ${H} Z`);
      dot.setAttribute("cx", head[0]);
      dot.setAttribute("cy", head[1]);
      ring.setAttribute("cx", head[0]);
      ring.setAttribute("cy", head[1]);
      const ph = ((t * 1.6) % 1 + 1) % 1;
      ring.setAttribute("r", U * (0.012 + 0.03 * ph));
      ring.style.opacity = String(1 - ph);
      pill.textContent = fmt(val, decimals) + unit;
      pill.style.left = `${head[0]}px`;
      pill.style.transform = head[0] > W * 0.8 ? "translate(-100%, -135%)" : "";
      pill.style.top = `${head[1]}px`;
      const shown = t >= points[0].at;
      for (const e of [line, dot, ring, pill]) e.style.visibility = shown ? "" : "hidden";
      if (fill) fill.style.visibility = shown ? "" : "hidden";
    });
    points.forEach((q, i) => vc.cue(i === points.length - 1 ? "impact" : "tick", q.at, { component: "lineChart", strength: i === points.length - 1 ? 0.6 : 0.3 }));
    return life(root, "lineChart", p, first(p.enter, p.at, points[0].at - 0.35), times, [pill]);
  }

  // Comparison: two columns; at `winAt` the loser dims, shrinks and is struck, the winner
  // grows, glows and gets a check badge. left/right: {title, items?: [text], value?, icon?}.
  function compare(parent, p = {}) {
    const { left, right, winner = "right", winAt, width = 0.84 } = p;
    const root = vc.el("div", "vc-cmp", parent);
    root.style.width = px(width);
    const sides = [left, right].map((s, i) => {
      const c = card(root, "side", true);
      if (s.icon) icon(s.icon, c, 0.05).style.color = "var(--vc-hi-text)";
      const ttl = vc.el("div", "ttl", c, s.title);
      if (s.value != null) {
        const v = vc.el("div", "vc-num big", c, String(s.value));
        v.style.fontSize = fz(0.09);
      }
      for (const it of s.items || []) {
        const r = vc.el("div", "it", c);
        icon("minus", r, 0.026);
        vc.el("span", "", r, it);
      }
      const at = first(s.at, p.at + i * 0.25);
      pop(c, at, { dir: i ? "left" : "right", component: "compare", strength: 0.45 });
      return { c, ttl, at };
    });
    if (p.vs !== false) {
      const vs = vc.el("div", "vs", root, p.vs || "vs");
      root.insertBefore(vs, sides[1].c);
      pop(vs, sides[1].at, { cue: false, scale: 0.4 });
    }
    const times = sides.map((s) => s.at);
    if (winAt != null) {
      const w = sides[winner === "left" ? 0 : 1];
      const l = sides[winner === "left" ? 1 : 0];
      vc.tl.add(l.c, { opacity: { to: 0.5 }, scale: { to: 0.9 }, filter: { to: "saturate(0.3)" }, ease: "out(3)", duration: D(380) }, ms(winAt));
      const sk = vc.el("span", "line", l.ttl);
      l.ttl.classList.add("vc-strike");
      sk.style.transform = "scaleX(0)";
      vc.tl.add(sk, { scaleX: { from: 0, to: 1 }, ease: "inOut(3)", duration: D(340) }, ms(winAt));
      vc.cue("strike", winAt, { component: "compare", strength: 0.5 });
      vc.tl.add(w.c, { scale: [{ to: 1.08 * (1 + 0.04 * S.overshoot), duration: D(160), ease: "out(2)" }, { to: 1.05, ease: vc.springPop }] }, ms(winAt + 0.12));
      w.c.classList.add("lg");
      setAt(w.c, "boxShadow", "var(--vc-shadow-lg), 0 0 0 " + px(0.004) + " var(--vc-hi)", winAt + 0.12);
      const badge = vc.el("div", "badge", w.c);
      icon("check", badge, 0.04, { stroke: 3 });
      pop(badge, winAt + 0.2, { scale: 0.2, cue: "impact", component: "compare", strength: 0.75 });
      times.push(winAt, winAt + 0.2);
    }
    return life(root, "compare", p, first(p.at, sides[0].at), times, sides.map((s) => s.c), { cue: false });
  }

  // Steps / timeline: nodes light up one after another, the track draws between them.
  // steps: [{title, sub?, at}].
  function steps(parent, p = {}) {
    const { width = 0.86 } = p;
    const root = card(parent, "", true);
    root.style.width = px(width);
    const row = vc.el("div", "vc-steps", root);
    const track = vc.el("div", "track", row);
    const fillEl = vc.el("i", "", track);
    const nodes = [];
    const times = [];
    p.steps.forEach((s, i) => {
      const st = vc.el("div", "st", row);
      const node = vc.el("div", "node", st, String(i + 1));
      const on = vc.el("div", "on", node);
      if (s.icon) icon(s.icon, on, 0.034, { stroke: 2.4 });
      else on.textContent = String(i + 1);
      vc.el("div", "t1", st, s.title);
      if (s.sub) vc.el("div", "t2", st, s.sub);
      pop(st, first(p.enter, p.at, p.steps[0].at - 0.3) + 0.1 + i * 0.08, { scale: 0.85, cue: false });
      vc.tl.add(on, { scale: { from: 0, to: 1, ease: vc.springPop } }, ms(s.at));
      pulse(node, s.at, 0.15);
      vc.cue(i === p.steps.length - 1 ? "check" : "tick", s.at, { component: "steps", strength: 0.5 });
      times.push(s.at);
      nodes.push(st);
    });
    // track: from the first node's centre to the last; fill reaches node i at its time
    const n = p.steps.length;
    vc.onSeek(() => {
      if (track.dataset.ok) return;
      const a = box(nodes[0].firstChild);
      const b = box(nodes[n - 1].firstChild);
      const r = box(row);
      Object.assign(track.style, { left: `${a.cx - r.x}px`, width: `${b.cx - a.cx}px`, top: `${a.cy - r.y}px` });
      track.dataset.ok = "1";
    });
    const ats = p.steps.map((s) => s.at);
    vc.onSeek((t) => {
      let k = 0;
      for (let i = 0; i + 1 < n; i++) {
        if (t >= ats[i + 1]) k = i + 1;
        else if (t > ats[i]) k = i + clamp((t - ats[i]) / Math.min(0.45 * S.dur, ats[i + 1] - ats[i]), 0, 1);
      }
      fillEl.style.transform = `scaleX(${(k / Math.max(1, n - 1)).toFixed(4)})`;
    });
    return life(root, "steps", p, first(p.enter, p.at, ats[0] - 0.3), times, nodes);
  }

  // Table: header first, then rows slide in on their words; `hi: true` rows light up.
  // columns: [text | {text, num}], rows: [{cells: [..], at, hi?}].
  function table(parent, p = {}) {
    const { columns, rows, width = 0.84, title } = p;
    const root = card(parent, "vc-table", true);
    root.style.width = px(width);
    if (title) vc.el("div", "vc-label", vc.el("div", "vc-kicker", root), title);
    const tbl = vc.el("table", "", root);
    const thead = vc.el("tr", "", vc.el("thead", "", tbl));
    const num = columns.map((c) => typeof c === "object" && c.num);
    columns.forEach((c, i) => vc.el("th", num[i] ? "num" : "", thead, typeof c === "object" ? c.text : c));
    const tb = vc.el("tbody", "", tbl);
    const trs = [];
    const times = [];
    for (const r of rows) {
      const tr = vc.el("tr", r.hi ? "hi" : "", tb);
      r.cells.forEach((c, i) => vc.el("td", num[i] ? "num" : "", tr, String(c)));
      pop(tr, r.at, { dir: "left", scale: 0.96, rise: 0.03, component: "table", cue: "tick", strength: 0.35 });
      if (r.hi) pulse(tr, r.at + 0.3, 0.04, { cue: "impact", component: "table", strength: 0.5 });
      times.push(r.at);
      trs.push(tr);
    }
    return life(root, "table", p, first(p.enter, p.at, rows[0].at - 0.35), times, trs);
  }

  // Chat bubbles (incoming / outgoing) with typing dots before each message.
  // messages: [{text, at, side: "in"|"out", typing?: s}]. Inside frame(...).body or alone.
  function chat(parent, p = {}) {
    const root = vc.el("div", "vc-chat", parent);
    if (p.width) root.style.width = px(p.width);
    const times = [];
    const msgs = p.messages.map((m) => {
      const typing = m.typing == null ? 0.45 : m.typing;
      const b = vc.el("div", `msg${m.side === "out" ? " out" : ""}`, root);
      const dots = vc.el("span", "dots", b);
      const dd = [0, 1, 2].map(() => vc.el("i", "", dots));
      const tx = vc.el("span", "", b, m.text);
      vc.onSeek((t) => {
        const on = t < m.at;
        dots.style.display = on ? "" : "none";
        tx.style.display = on ? "none" : "";
        dd.forEach((d, i) => (d.style.opacity = String(0.35 + 0.65 * Math.max(0, Math.sin((t * 7 - i * 0.9) % (Math.PI * 2))))));
      });
      if (typing > 0) {
        pop(b, m.at - typing, { scale: 0.6, dir: "up", component: "chat", cue: false });
        pulse(b, m.at, 0.05, { cue: "pop", component: "chat", strength: 0.45 });
      } else pop(b, m.at, { scale: 0.6, component: "chat", strength: 0.45 });
      times.push(m.at - typing, m.at);
      return b;
    });
    if (p.at != null || p.after) return life(root, "chat", p, first(p.at, p.messages[0].at - 0.5), times, msgs, { cue: false });
    return root;
  }

  // File list rows (generic file browser): [{name, meta?, at, kind: "file"|"folder", indent?, hi?}].
  function fileList(parent, p = {}) {
    const root = vc.el("div", "vc-files", parent);
    if (p.width) root.style.width = px(p.width);
    const times = [];
    const rows = p.files.map((f) => {
      const r = vc.el("div", `f${f.hi ? " hi" : ""}`, root);
      r.style.paddingLeft = px(0.012 + (f.indent || 0) * 0.03);
      icon(f.kind === "folder" ? "folder" : "file", r, 0.03);
      vc.el("span", "nm", r, f.name);
      if (f.meta) vc.el("span", "mt", r, f.meta);
      pop(r, f.at, { dir: "left", scale: 0.95, rise: 0.02, component: "fileList", cue: "tick", strength: 0.25 });
      if (f.hi) setAt(r, "className", "f hi", f.at);
      times.push(f.at);
      return r;
    });
    if (p.at != null || p.after) return life(root, "fileList", p, first(p.at, p.files[0].at - 0.3), times, rows, { cue: false });
    return root;
  }

  // App card: an icon tile, a title, a meta line (optional star rating filling up on
  // `ratingAt`) and an action button pressed on `actionAt`.
  function appCard(parent, p = {}) {
    const root = card(parent, "vc-app", true);
    if (p.width) root.style.width = px(p.width);
    const tile = vc.el("div", "tile", root);
    icon(p.icon || "spark", tile, 0.065, { stroke: 2.2 });
    const tx = vc.el("div", "tx", root);
    vc.el("div", "t1", tx, p.title || "");
    const meta = vc.el("div", "t2", tx);
    const times = [];
    if (p.rating != null) {
      const st = vc.el("div", "stars", meta);
      const n = p.stars || 5;
      const ra = first(p.ratingAt, p.at + 0.4);
      for (let i = 0; i < n; i++) {
        const s = icon("star", st, 0.032);
        if (i < p.rating) {
          setAt(s, "color", "var(--vc-hi-text)", ra + i * 0.11 * S.dur);
          setAt(s, "fill", "currentColor", ra + i * 0.11 * S.dur);
          pulse(s, ra + i * 0.11 * S.dur, 0.35, { cue: "tick", component: "appCard", strength: 0.25 });
          times.push(ra + i * 0.11 * S.dur);
        }
      }
    }
    if (p.meta) vc.el("span", "", meta, p.meta);
    let btn = null;
    if (p.action) {
      btn = vc.el("div", "btn", root, p.action);
      if (p.actionAt != null) {
        vc.tl.add(btn, { scale: [{ to: 0.86, duration: D(110), ease: "out(2)" }, { to: 1, ease: vc.springPop }] }, ms(p.actionAt));
        vc.cue("tick", p.actionAt, { component: "appCard", strength: 0.5 });
        times.push(p.actionAt);
      }
    }
    pulse(tile, p.at + 0.2, 0.1);
    return life(root, "appCard", p, p.at, times, [tile, btn].filter(Boolean));
  }

  // Progress / meter: a bar (kind "bar") or ring (kind "ring") filling to `value` of `max`
  // with the number counting along. {value, max=100, at, dur, label, unit}.
  function meter(parent, p = {}) {
    const { value, max = 100, dur = 1, label, unit = "%", kind = "bar", decimals = 0, width = 0.7 } = p;
    const root = card(parent, "vc-meter", true);
    const k = clamp(value / max, 0, 1);
    const ease = A.eases.out(3);
    const at = p.at;
    let num;
    if (kind === "ring") {
      root.style.display = "flex";
      root.style.alignItems = "center";
      root.style.gap = px(0.04);
      const R = U * 0.09;
      const sw = U * 0.022;
      const svg = vc.el("svg", "", root);
      const sz = 2 * R + sw * 2;
      svg.setAttribute("width", sz);
      svg.setAttribute("height", sz);
      const bg = vc.el("circle", "rbg", svg);
      const fg = vc.el("circle", "rfg", svg);
      for (const c of [bg, fg]) {
        c.setAttribute("cx", sz / 2);
        c.setAttribute("cy", sz / 2);
        c.setAttribute("r", R);
        c.setAttribute("stroke-width", sw);
      }
      fg.setAttribute("transform", `rotate(-90 ${sz / 2} ${sz / 2})`);
      fg.setAttribute("pathLength", "1");
      fg.style.strokeDasharray = "1";
      const tx = vc.el("div", "", root);
      num = vc.el("div", "vc-num", tx);
      num.style.fontSize = fz(0.11);
      if (label) vc.el("div", "vc-label", tx, label);
      vc.onSeek((t) => {
        const q = ease(clamp((t - at) / dur, 0, 1)) * k;
        fg.style.strokeDashoffset = String(1 - q);
        num.textContent = fmt((q / (k || 1)) * value, decimals) + unit;
      });
    } else {
      root.style.width = px(width);
      const top = vc.el("div", "top", root);
      vc.el("div", "vc-label", top, label || "");
      num = vc.el("div", "vc-num", top);
      num.style.fontSize = fz(0.08);
      const tr = vc.el("div", "track", root);
      const f = vc.el("i", "", tr);
      vc.onSeek((t) => {
        const q = ease(clamp((t - at) / dur, 0, 1));
        f.style.transform = `scaleX(${(q * k).toFixed(4)})`;
        num.textContent = fmt(q * value, decimals) + unit;
      });
    }
    countCues("meter", at, dur, Math.min(value, 12));
    pulse(num, at + dur, 0.08);
    return life(root, "meter", p, first(p.enter, at - 0.3), [at, at + dur], [num]);
  }

  // Chips / tags: pills with an icon popping in one by one; the newest one carries a ring
  // that moves on with the next. chips: [{text, icon?, at}], label.
  function chips(parent, p = {}) {
    const root = vc.el("div", "vc-chips", parent);
    if (p.width) root.style.width = px(p.width);
    const times = [];
    const els = p.chips.map((c, i) => {
      const e = vc.el("div", "vc-chip", root);
      if (c.icon) icon(c.icon, e, 0.044, { stroke: 2.2 });
      vc.el("span", "", e, c.text);
      const ring = vc.el("div", "ring", e);
      pop(e, c.at, { scale: 0.4, rise: 0.05, component: "chips", strength: 0.45 });
      const next = p.chips[i + 1] ? p.chips[i + 1].at : c.at + 1.2;
      vc.tl.add(ring, { opacity: [{ from: 0, to: 1, duration: D(150) }, { to: 0, duration: D(250), delay: Math.max(0, ms(next - c.at) - D(400)) }] }, ms(c.at));
      times.push(c.at);
      return e;
    });
    if (p.allAt != null) {
      els.forEach((e, i) => pulse(e, p.allAt + i * 0.06, 0.08));
      vc.cue("impact", p.allAt, { component: "chips", strength: 0.55 });
      times.push(p.allAt);
    }
    return life(root, "chips", p, first(p.at, p.chips[0].at - 0.05), times, els, { cue: false, drift: 0.01 });
  }

  // Quote card: a big quote mark, the text lighting up word by word on its times (`words`:
  // [{text, at}]) or at once (`text`), the attribution line under it.
  function quote(parent, p = {}) {
    const root = card(parent, "vc-quote", true);
    if (p.width) root.style.width = px(p.width);
    const qm = icon("quote", root, 0.07, { stroke: 2.4 });
    qm.classList.add("qm");
    const qt = vc.el("div", "qt", root);
    const times = [];
    const spans = [];
    if (p.words) {
      p.words.forEach((w, i) => {
        const s = vc.el("span", "", qt, (i ? " " : "") + w.text);
        s.style.opacity = "0.18";
        vc.tl.add(s, { opacity: { from: 0.18, to: 1 }, ease: "out(2)", duration: D(180) }, ms(w.at));
        if (w.mark) {
          setAt(s, "color", "var(--vc-hi-text)", w.at);
          setAt(s, "textShadow", "var(--vc-text-glow)", w.at);
          vc.cue("impact", w.at, { component: "quote", strength: 0.45 });
        }
        times.push(w.at);
        spans.push(s);
      });
    } else qt.textContent = p.text || "";
    if (p.by) {
      const by = vc.el("div", "by", root);
      vc.el("i", "", by);
      vc.el("span", "", by, p.by);
    }
    pulse(qm, first(p.at, 0) + 0.15, 0.2);
    return life(root, "quote", p, first(p.at, p.words ? p.words[0].at - 0.3 : 0), times, [qm], { cue: "whoosh", strength: 0.5 });
  }

  // Pointer toward the speaker (default: down from the band onto the face) with a label;
  // it bounces on every beat while on screen. {at, dir: "down"|"left"|"right"|"up", label}.
  function pointer(parent, p = {}) {
    const dir = p.dir || "down";
    const root = vc.el("div", "vc-ptr", parent);
    if (dir === "left" || dir === "right") root.style.flexDirection = "row";
    let lab = null;
    if (p.label) {
      lab = vc.el("div", "vc-pill solid", root, p.label);
      lab.style.fontSize = fz(0.04);
    }
    const arw = icon(`arrow-${dir === "left" ? "right" : dir}`, root, p.size || 0.11, { stroke: 2.6 });
    arw.classList.add("arw");
    if (dir === "left") {
      arw.style.transform = "scaleX(-1)";
      root.insertBefore(arw, root.firstChild);
    }
    if (dir === "up") root.insertBefore(arw, root.firstChild);
    const end = p.out != null ? p.out : vc.duration;
    const ax = { down: ["y", 1], up: ["y", -1], left: ["x", -1], right: ["x", 1] }[dir];
    for (let t = p.at + 0.35, i = 0; t < end - 0.2; t += S.beat * 1.5, i++)
      vc.tl.add(arw, { [ax[0]]: [{ to: ax[1] * 0.025 * U, duration: D(170), ease: "out(2)" }, { to: 0, duration: D(260), ease: "inOut(2)" }] }, ms(t));
    return life(root, "pointer", { ...p, micro: false }, p.at, [], [], { cue: "whoosh", strength: 0.5, drift: 0 });
  }

  // Image / material frame: an approved still (`src`: a file inside the job folder) in a
  // frame with depth: the picture pans and zooms slowly while the frame and the caption
  // plate move at other rates (parallax). {src, at, width, height, caption, pan: [dx, dy]}.
  function imageFrame(parent, p = {}) {
    const root = card(parent, "vc-img", true);
    const win = vc.el("div", "win", root);
    win.style.width = px(p.width || 0.6);
    win.style.height = px(p.height || 0.34);
    const img = vc.el("img", "", win);
    img.setAttribute("src", `/job/${String(p.src).split("/").map(encodeURIComponent).join("/")}`);
    img.setAttribute("alt", "");
    fx(win);
    const end = p.out != null ? p.out : vc.duration;
    const [dx, dy] = p.pan || [-0.04, -0.02];
    vc.tl.add(img, { scale: { from: 1.0, to: 1.12 }, x: { from: 0, to: dx * U }, y: { from: 0, to: dy * U }, ease: "linear", duration: ms(end - p.at) }, ms(p.at));
    vc.tl.add(root, { rotateY: { from: -4, to: 4 }, rotateX: { from: 2, to: -1 }, ease: "inOut(1)", duration: ms(end - p.at) }, ms(p.at));
    const times = [];
    if (p.caption) {
      const cap = vc.el("div", "vc-pill solid cap", root, p.caption);
      cap.style.fontSize = fz(0.032);
      pop(cap, p.at + 0.35, { dir: "right", component: "imageFrame", strength: 0.3 });
      vc.tl.add(cap, { x: { from: 0, to: -dx * U * 0.6 }, ease: "linear", duration: ms(end - p.at - 0.6) }, ms(p.at + 0.6));
      times.push(p.at + 0.35);
    }
    root.style.transformStyle = "preserve-3d";
    root.parentElement.style.perspective = px(1.6);
    return life(root, "imageFrame", p, p.at, times, [], { cue: "whoosh", strength: 0.55 });
  }

  // Tilted plane (CSS 3D, crisp text): any element on a plane in perspective with a camera
  // move from `from` to `to` ({rx, ry, rz, z} in degrees / units) between at and until.
  function tilt(el, p = {}) {
    const wrap = vc.el("div", "vc-tilt", el.parentElement);
    el.parentElement.insertBefore(wrap, el);
    const plane = vc.el("div", "plane", wrap);
    plane.appendChild(el);
    const f = { rx: 18, ry: -14, rz: 2, z: -0.1, ...(p.from || {}) };
    const g = { rx: 8, ry: 6, rz: -1, z: 0, ...(p.to || {}) };
    const t0 = p.at;
    const t1 = first(p.until, p.out, vc.duration);
    vc.tl.add(
      plane,
      { rotateX: { from: f.rx, to: g.rx }, rotateY: { from: f.ry, to: g.ry }, rotateZ: { from: f.rz, to: g.rz }, z: { from: f.z * U, to: g.z * U }, ease: "inOut(2)", duration: ms(t1 - t0) },
      ms(t0),
    );
    return wrap;
  }

  // --- 3D (Three.js, WebGL) ----------------------------------------------------------
  // Rendered in onSeek (never requestAnimationFrame), preserveDrawingBuffer so the frame
  // capture reads the drawn picture. Colours come from the resolved style (hex strings).

  // A Three.js colour from a resolved style hex (mk() with no argument: the neutral
  // multiplier for per-instance colours).
  const mk = (v) => {
    const K = vc.THREE.Color;
    return v == null ? new K(1, 1, 1) : new K(v);
  };

  function gl(parent, w, h, antialias = true) {
    const T = vc.THREE;
    if (!T) throw new Error("3D component without Three.js: the renderer loads it for scenes that call a *3d component");
    const holder = vc.el("div", "vc-3d", parent);
    const canvas = vc.el("canvas", "", holder);
    const r = new T.WebGLRenderer({ canvas, alpha: true, antialias, preserveDrawingBuffer: true, powerPreference: "high-performance" });
    r.setPixelRatio(1);
    r.setSize(w, h, false);
    r.setClearColor(0, 0);
    return { T, r, holder, canvas };
  }

  // A soft round sprite (white, alpha = coverage) for point fields.
  function dotTexture(T) {
    const c = document.createElement("canvas");
    c.width = c.height = 64;
    const g = c.getContext("2d");
    const img = g.createImageData(64, 64);
    for (let y = 0; y < 64; y++)
      for (let x = 0; x < 64; x++) {
        const d = Math.hypot(x - 31.5, y - 31.5) / 32;
        const i = (y * 64 + x) * 4;
        img.data[i] = img.data[i + 1] = img.data[i + 2] = 255;
        img.data[i + 3] = Math.round(255 * clamp(1.4 - d * 1.6, 0, 1) ** 1.5);
      }
    g.putImageData(img, 0, 0);
    return new T.CanvasTexture(c);
  }

  // Render a GL scene only while its holder can be seen (frame cost matters on SwiftShader).
  const live = (t, t0, t1) => t >= t0 - 0.05 && t <= t1 + 0.5;

  // Particle field: a floor of glowing points (kind "grid") or a drifting cloud (kind
  // "particles") receding into fog, the camera gliding forward. Put it first in the slot
  // (or the stage) so it sits behind the cards. {at, out, kind, count, speed}.
  function field3d(parent, p = {}) {
    const w = Math.round(parent.offsetWidth || vc.region[2] - vc.region[0]);
    const h = Math.round(parent.offsetHeight || vc.region[3] - vc.region[1]);
    const { T, r, holder } = gl(parent, w, h, false);
    const scene = new T.Scene();
    const cam = new T.PerspectiveCamera(55, w / h, 0.1, 200);
    const accent = mk(S.colors.accent);
    const ink = mk(S.colors.ink);
    scene.fog = new T.Fog(mk(S.colors.panel), 6, 46);
    const kind = p.kind || "grid";
    const n = p.count || (kind === "grid" ? 9000 : 2400);
    const pos = new Float32Array(n * 3);
    const colr = new Float32Array(n * 3);
    const side = Math.round(Math.sqrt(n));
    const rnd = (a, b) => vc.random(a, b, 4);
    const c0 = mk();
    for (let i = 0; i < n; i++) {
      let x;
      let y;
      let z;
      if (kind === "grid") {
        x = ((i % side) - side / 2) * 0.55;
        z = -Math.floor(i / side) * 0.55;
        y = -2.2 + Math.sin(x * 0.45) * 0.12 + Math.cos(z * 0.35) * 0.12;
      } else {
        x = rnd(-14, 14);
        y = rnd(-6, 8);
        z = rnd(-60, 2);
      }
      pos.set([x, y, z], i * 3);
      c0.copy(ink).lerp(accent, kind === "grid" ? 0.9 : rnd(0.3, 1));
      colr.set([c0.r, c0.g, c0.b], i * 3);
    }
    const geo = new T.BufferGeometry();
    geo.setAttribute("position", new T.BufferAttribute(pos, 3));
    geo.setAttribute("color", new T.BufferAttribute(colr, 3));
    const mat = new T.PointsMaterial({ size: kind === "grid" ? 0.26 : 0.3, map: dotTexture(T), vertexColors: true, transparent: true, depthWrite: false, blending: T.AdditiveBlending, fog: true });
    scene.add(new T.Points(geo, mat));
    const speed = (p.speed || 1.6) / S.dur;
    const start = first(p.at, 0);
    const end = first(p.out, vc.duration);
    vc.onSeek((t) => {
      if (!live(t, start, end)) return;
      const u = Math.max(0, t - start);
      const travel = (u * speed) % (kind === "grid" ? 0.55 * 10 : 40);
      cam.position.set(Math.sin(u * 0.25) * 0.6, kind === "grid" ? -0.7 : 0.5, 2 - travel);
      cam.lookAt(0, kind === "grid" ? -1.5 : 0.2, -20 - travel);
      r.render(scene, cam);
    });
    pop(holder, start, { scale: 1.1, rise: 0, blur: 0.01, cue: p.cue === false ? false : "whoosh", component: "field3d", strength: 0.35, soft: true });
    if (p.out != null) leave(holder, p.out, { component: "field3d", cue: false });
    return holder;
  }

  // Extruded number or word: the text in the brand font, stacked into a solid with lit
  // sides, swinging in on the spring and orbiting slowly. {text, at, out, size, depth}.
  function number3d(parent, p = {}) {
    const w = Math.round(parent.offsetWidth || vc.region[2] - vc.region[0]);
    const h = Math.round(parent.offsetHeight || vc.region[3] - vc.region[1]);
    const { T, r, holder } = gl(parent, w, h);
    const text = String(p.text);
    // the glyphs, drawn once into a mask texture in the page's brand font
    const c2 = document.createElement("canvas");
    const fsz = 512;
    const g = c2.getContext("2d");
    const font = `${getComputedStyle(document.documentElement).getPropertyValue("--vc-w-strong").trim() || 800} ${fsz}px ${getComputedStyle(document.body).fontFamily}`;
    g.font = font;
    const tw = Math.ceil(g.measureText(text).width) + 40;
    c2.width = tw;
    c2.height = Math.round(fsz * 1.2);
    g.font = font;
    g.textBaseline = "middle";
    g.textAlign = "center";
    g.fillStyle = S.colors.ink;
    g.fillText(text, tw / 2, c2.height / 2);
    // a pure mask: full-bright pixels, alpha = glyph coverage (the layers tint it)
    const img = g.getImageData(0, 0, c2.width, c2.height);
    for (let i = 0; i < img.data.length; i += 4) img.data[i] = img.data[i + 1] = img.data[i + 2] = 255;
    g.putImageData(img, 0, 0);
    const tex = new T.CanvasTexture(c2);
    tex.colorSpace = T.SRGBColorSpace;
    const aspect = tw / c2.height;
    const H = 1.6 * (p.size || 1);
    const layers = Math.max(8, Math.round((p.depth || 0.35) * 120));
    const geo = new T.PlaneGeometry(H * aspect, H);
    const group = new T.Group();
    const front = mk(S.colors.accent);
    const sideC = mk(S.colors.accent).lerp(mk(S.colors.panel), 0.55);
    const D3 = (p.depth || 0.35) * H;
    for (let i = layers; i >= 0; i--) {
      const m = new T.MeshBasicMaterial({ map: tex, transparent: true, alphaTest: 0.5, color: i === 0 ? front : sideC.clone().lerp(front, (1 - i / layers) * 0.35) });
      const q = new T.Mesh(geo, m);
      q.position.z = -(i / layers) * D3;
      group.add(q);
    }
    const scene = new T.Scene();
    scene.add(group);
    const cam = new T.PerspectiveCamera(32, w / h, 0.1, 50);
    cam.position.set(0, 0, 5.2);
    const spring = vc.springPop;
    const at = p.at;
    const end = first(p.out, vc.duration);
    vc.onSeek((t) => {
      if (!live(t, at, end)) return;
      const u = t - at;
      const k = u <= 0 ? 0 : spring.ease(clamp(u / (spring.settlingDuration / 1000), 0, 1));
      group.rotation.y = (1 - k) * -1.3 + Math.sin(Math.max(0, u) * 0.9) * 0.28;
      group.rotation.x = (1 - k) * 0.5 + 0.12 + Math.sin(Math.max(0, u) * 0.6) * 0.06;
      group.scale.setScalar(0.5 + 0.5 * k);
      r.render(scene, cam);
    });
    hide(holder);
    vc.tl.add(holder, { opacity: { from: 0, to: 1, duration: D(200), ease: "out(2)" } }, ms(at));
    vc.cue("impact", at, { component: "number3d", strength: 0.8 });
    if (p.out != null) leave(holder, p.out, { component: "number3d" });
    return holder;
  }

  return {
    // vocabulary
    setAt, pop, leave, morph, pulse, nudge, drift, sheen, micro, slot, icon, ICONS: Object.keys(ICONS),
    // components
    headline, stat, counter, checklist, strikeCard, terminal, frame, barChart, lineChart, compare, steps, table,
    chat, fileList, appCard, meter, chips, quote, pointer, imageFrame, tilt, field3d, number3d,
  };
};
