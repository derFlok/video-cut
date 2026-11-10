# Spike: Claude-authored motion scenes

Status: prototype on branch `derFlok/motion-spike`, not merged. Question: can Claude write
a small motion scene per beat (HTML/SVG/CSS) that our renderer renders deterministically,
synced to word times and composited over the speaker, at an animation level well above
the fixed canvas overlay templates? And what does it cost?

Short answer: yes. The runtime, sandbox and five components work, the demo renders in
about real time, and four render-inspect rounds made the scene clean. The open points
are motion density, how captions and the motion band share the frame in close-ups, and
3D/SFX, which this spike did not build.

## What was built

| Path | What |
|---|---|
| `renderer/motion.js` | CLI: `node motion.js <motion.json> [--out mov] [--stills dir t,t,…] [--region-only] [--frame-timeout ms]`; sandboxed page, one sequential pass over the frame grid, ProRes 4444 with alpha |
| `renderer/motion/runtime.js` | page runtime: virtual clock, seeded randomness, frozen timers, network APIs that throw, brand tokens as CSS custom properties, the word/beat API, `seek(t)` |
| `renderer/motion/components.js` | the five components plus `pop`/`leave`/`drift`/`setAt` |
| `renderer/lib/literals.js` | "brand tokens only" check for agent-written scene modules (the same rules as the lint test) |
| `renderer/lib/ffmpeg.js` | `startFfmpeg`/`rate` moved out of `render.js` (shared, no behaviour change) |
| `renderer/test/motion.test.js`, `test/motion/gallery.*` | sandbox, determinism, beat and literal tests; a neutral gallery of all five components |
| `scripts/motion_spike.py` | builds `work/motion/motion.json` for a span of a finished job (words in scene time, region from the face track, resolved brand tokens) |

Dependency: `animejs` 4.5.0 (MIT), pinned with an integrity hash in
`renderer/package-lock.json` and served to the page from `node_modules`, so nothing is
fetched at render time.

## API sketch

The motion spec (`work/motion/motion.json`):

```json
{"mode": "motion", "size": [1080, 1920], "fps": 30, "duration": 20.7, "unit": 1080,
 "region": [65, 230, 1015, 635], "scene": "work/motion/scene.js", "seed": 1,
 "words": [{"w": "…", "t0": 0.07, "t1": 0.5}],
 "beats": {"check_1": {"word": "…", "n": 1, "edge": "start|end", "offset": 0.35}},
 "tokens": {"…resolved brand tokens…": true}}
```

The beat sheet (`beats`) maps an event id to a spoken word or phrase: n-th occurrence,
start or end, and an offset. The renderer resolves it before the page starts, and an
unknown word fails the render. The beat sheet is also the part a human or a plan gate can
review.

The scene module (`export default function (vc) {…}`), written by Claude per span:

```js
export default function scene(vc) {
  const { c, beat: b, stage } = vc;           // components, beat times, the stage (region)
  c.headline(stage, { words: [{ text: "…", at: b("w1") }, …], mark: [{ index: 2, at: b("w3"), style: "box" }], card: true, out: b("next") - 0.25 });
  c.checklist(stage, { label: "…", columns: 2, items: [{ text: "…", at: b("i1"), check: b("i1") + 0.35 }, …] });
  c.counter(stage, { from: 0, to: 3, at: b("drei"), suffix: "…", label: "…" });
  c.strikeCard(stage, { text: "…", at: …, strikeAt: b("x"), then: "…", thenAt: b("y") });
  c.terminal(stage, { lines: [{ text: "…", at: 0.8 }, { text: "…", at: 1.6, out: true }] });
  vc.tl.add(el, { scale: [{ to: 1.06 }, { to: 1 }] }, vc.ms(b("z")));  // raw anime.js on the master timeline
  vc.onSeek((t) => { /* pure function of t for text/digits */ });
}
```

`vc` also carries `at(word, n)`, `end(word, n)`, `words`, `ms(s)`, `spring` (the brand
spring as an anime.js spring), `random` (seeded), `el(tag, class, parent, text)`, `css(text)`,
`size`, `region`, `unit` and `tokens`.

The seek contract: `window.__vc.seek(t)` seeks the anime.js master timeline (built with
`autoplay: false`, never played), runs the `onSeek` functions and pauses and seeks every
CSS/WAAPI animation. CSS transitions are switched off. `Date.now` and `performance.now`
return the seek time, `setTimeout`, `setInterval` and `requestAnimationFrame` never fire,
and `Math.random` is seeded. Tested: the DOM after `seek(t)` is the same whatever was
sought before.

Colours and fonts come only from tokens: `--vc-bg/surface/text/muted/accent/accent-text/
on-accent/dark/dark-text/font/w-*`. `--vc-on-accent` is whichever of text and surface
contrasts more with the accent. The renderer's lint test covers `renderer/motion/`.
`motion.js` runs the same check on the scene module before it loads, and refuses a hex
colour, a colour function, a colour name or a font name.

## Sandbox

The scene is untrusted. The page lives on a private origin (`https://scene.invalid`):

- every request goes through the renderer's request filter. The page, the runtime, the
  brand fonts and files inside the job folder (realpath-confined) are served; everything
  else is aborted and **fails the render**. That includes a blocked request the scene
  swallowed in a `try`;
- CSP: `default-src 'none'; script-src 'self'; connect-src 'none'; worker-src 'none'; …`.
  A CSP violation fails the render;
- `fetch`, `XMLHttpRequest`, `WebSocket`, `EventSource`, `Worker`, `WebTransport`,
  `RTCPeerConnection` and `sendBeacon` throw; WebSockets are also closed by
  `routeWebSocket`; DNS is mapped to nothing (`--host-resolver-rules`); service workers
  are blocked;
- the page has no Node APIs (plain page, no `exposeFunction`); scene setup has a 10 s
  limit and every frame a 2 s limit (seek and capture), so an endless loop fails the
  render.

Tests (`renderer/test/motion.test.js`) cover each of these failing closed with no frame
written: fetch, a swallowed fetch, XHR, WebSocket, `import()` over http, an `<img>` and a
CSS background over https, a path out of the job folder, a scene module outside the job
folder, and a frame that never returns.

## Determinism

The DOM state is a pure function of t. The **pixels** are not quite: Chromium keeps
rasterised layers between frames. A text layer that was drawn at scale 1.0001 and then
set to exactly 1 is not drawn again, so a frame differs by a few anti-aliasing levels
(max 69 of 255, about 0.04 % of the channels) depending on which frames came before it.
Compositor flags and `will-change` did not change this. The fix in the spike: there is
only one rendering path. It is one sequential pass from frame 0, and stills are taken
from that pass. Three full renders gave identical `framemd5` hashes, and a still is
bit-identical to the clip frame at the same time (tested).

## Licence finding

- **anime.js 4.5.0, MIT**: adopted. Its timeline seeks exactly with `autoplay: false`, and
  it has springs, SVG drawing and a seeded random.
- **GSAP 3.15 ("Standard 'no charge' license", Webflow, effective 2025-04-30): not
  acceptable.** It is not an open-source licence, and Webflow keeps all rights. Its
  Prohibited Uses include "any implementation and/or use of GSAP Products in tools that
  allow users to build visual animations without code" and anything that "competes with
  Webflow's visual animation building capabilities". Video Cut lets a person get animated
  video without writing code, so it falls under that clause. The licence also says nothing
  about redistribution in an Apache-2.0 project. Not adopted.
- Motion (MIT) and Three.js (MIT) remain options. Three.js would be the 3D route (below).

## Demo

The real recording (job copied to `~/VideoCut/jobs/20261109-232934-motion-spike`; the
original job was not touched). Span: output 1.9–22.6 s (20.7 s), a talking-head
opinion piece with a list of five things, a small number and a turn of mind. The beat
sheet has 26 events. There are six moments, each
transforming into the next:

1. a kinetic headline on a card with an underline on the keyword;
2. a second headline built word by word with an accent box on the last word;
3. a 2-column checklist: each item appears on its word and is checked 0.35 s later, with
   a running tally (1/5 … 5/5) and a nod of the whole card on the closing phrase;
4. a counter rolling to the spoken number on its word, which moves aside for a
   strike-through card (an option struck on one word, the answer popping in on another);
5. a 2-item checklist;
6. a closing headline with an accent box.

The terminal is in the gallery only: a terminal does not fit this content, and Claude
would not pick it here.

Layout as in the reference: speaker full-frame 9:16 and graphics in a band above the head.
The band is `[65, 230, 1015, 635]`: below the platform's top bar and above the highest
face top over the span plus 20 % of the face height (hair and forehead may be covered,
eyes not). The existing captions stay. They are rendered again by the canvas renderer with
the band as a hard block, so they keep out of it.

Files (outside the repository, never committed): `out/motion_demo.mp4` (the final 20.7 s
reel), `work/motion/scene.js`, `beats.json`, `motion.json`,
`work/motion/stills/r1…r4/` (one frame per event + `sheet.jpg` per round).

## Render-inspect rounds

Four rounds: render, composite, one still per event 0.3 s after it, read the sheet, fix.

| Round | Found | Fixed |
|---|---|---|
| (gallery) | card shadow painted grey over the card (negative z-index inside the pop's stacking context); highlight marks invisible (a CSS rule for the word text matched the mark too); terminal prompt invisible when accent = dark | shadow as `box-shadow` with `color-mix` (alpha from tokens); separate text class; prompt as dimmed text |
| (captions) | with the band blocked, 3 caption groups went to the top or onto the eyes (close-up segment: no face-free room below) | band as a hard block; for the caption layout the face counts down to 72 % of its height (captions may cover the chin) |
| 1 | headlines without a card unreadable on hair; two headlines wrapped onto the forehead; labels wrapped onto two lines; a sliver of the next digit above the counter | headline `card: true`; smaller type; `nowrap`; counter rows 1.2 em |
| 2 | headline type too big (wrapped, ragged); labels small | headline 0.068 of the short side, labels 0.04 |
| 3 | headlines now too small for the reel | headline 0.08–0.082 (two lines, final layout reserved, no reflow) |
| 4 | clean: face free, no overlap with captions, every event on its word | — |

## Numbers

Apple M3 Pro, 1080×1920, 30 fps, 20.7 s = 621 frames. Two runs each (the second within
2 % of the first); wall time includes browser start; peak memory is the RSS of the whole
process tree (node + Chromium + ffmpeg), sampled every 0.1 s.

| Path | Wall | Frames/s | Peak memory |
|---|---|---|---|
| current canvas overlay (`render.js`, captions + 2 keyword cards, same span) | 23.3 s | 26.7 | 1.42–1.48 GB |
| motion scene, full frame captured | 27.8–28.2 s | 22.2 | 1.37 GB |
| motion scene, `--region-only` (1080×536 captured, placed at y 164) | 22.9–23.3 s | 26.9 | 0.75–0.81 GB |

Per frame (region-only): ~35 ms seek + capture in the page, encoding overlaps. The motion
clip comes in addition to the captions overlay (captions stay on the canvas path), so a
reel with motion costs about two overlay passes. Both can run at the same time; this was
not measured. Composite + H.264 of the 20.7 s span: 24 s (the same as a normal render).

**Motion density:** in the ffmpeg `freezedetect` of the motion clip, the longest stretch
with almost no change is 1.07 s (−60 dB) to 1.63 s (−45 dB). Several stretches of 0.8–1.2 s
come mostly between list items, where only the slow drift moves. That misses the target
of a change every 0.3–0.5 s. The scene needs more word-synced micro-events (see below).

**WebGL headless on this Mac:** works. The pinned headless shell gives WebGL 2 by default
on **SwiftShader** (software, ANGLE/Vulkan). With `--use-angle=metal` it uses the GPU
(ANGLE Metal, Apple M3 Pro). A trivial draw + readback works in both. A real Three.js
scene was **not** measured. On CI Linux (no GPU), SwiftShader is the same software path
(`--use-angle=swiftshader --enable-unsafe-swiftshader`): deterministic, but CPU cost
grows with scene complexity. For the frame-exact check, the GPU path (Metal) should be
tested for determinism separately before it is used.

## What a full block needs

- **Pipeline:** a `motion` tool (`videocut tool motion`) that builds motion.json from the
  EDL/overlays (what `scripts/motion_spike.py` does), renders per span with `--region-only`,
  records the SHA-256 outside the job folder (like slides), and makes `render` composite
  the clips at their offset (one more `overlay` in `composite_graph`). Verify needs boxes:
  the stage region per span, `overlay_on_face`, overlap with captions.
- **Captions and the band:** a caption rule for close-ups (the face fills the frame and the
  band takes the top): chin/neck band or captions hidden while a motion scene speaks for
  them. Today's layout put 3 groups on the eyes before the spike's workaround.
- **Planning skill changes:** the plan proposes motion moments (which span, which
  component, why) next to overlays and slides. The beat sheet is plan data and is
  approved with the plan. The overlay run writes the scene module, renders stills at every
  beat, inspects them, fixes them (this demo took 4 rounds) and keeps a report like
  `overlay_report.md`. Rules for the skill: one component
  on screen at a time in the band, headlines on a card, an event at least every 0.5 s
  (micro-events: per-word emphasis, tallies, nudges), leave before the next component enters.
- **Components still missing** (seen in the reference reels): table with rows building,
  bar/line chart growing on words, comparison (two columns, one wins), timeline/steps,
  quote card, icon + label chips, a progress/meter, a "before → after" morph, number
  with a unit change (e.g. € → %), screenshot/UI frame rebuilt from parts, arrows/pointers
  to the speaker, emoji-free icon set from the brand (or none).
- **3D:** Three.js (MIT) in the same page and the same seek contract (the scene's render
  loop driven by `onSeek`, with no `requestAnimationFrame`). Measure SwiftShader cost per
  frame and Metal determinism first; probably a quality option rather than the default.
- **SFX:** whooshes/ticks on events are an audio track built from the beat sheet (times
  are known exactly). It needs licensed or generated sounds (the same rules as music: no
  bundled third-party media; the user uploads or we generate). Mixed under the voice with
  ducking.
- **Speed:** region-only capture is the main lever (it halves memory). Further levers:
  JPEG capture is not possible because of alpha; `HeadlessExperimental.beginFrame` would
  allow deterministic frame control; render spans in parallel pages.

## Risks

- **Pixel determinism depends on draw order** (see above). Any later frame-skipping
  optimisation (reusing static frames, rendering only some frames for a preview) changes
  pixels slightly. Keep one sequential path, or accept "visually identical" in verify.
- **Untrusted code in a browser:** the sandbox is defence in depth (request filter + CSP
  + throwing APIs + DNS mapping + time limits). A Chromium exploit would still run in the
  renderer process. Keep the pinned browser current and never `exposeFunction` into the
  page.
- **Agent quality varies:** without the inspect loop, the first round was not shippable
  (illegible headlines, wrapping). The loop is not optional, and it costs tokens (one
  contact sheet per round).
- **Brand fit:** components use only tokens, but some brands' accent on surface has low
  contrast. The runtime chooses the text colour on accent fills; accent text on a surface
  uses the contrast-checked caption highlight.
- **Fonts:** a brand without font files falls back to the system UI font in the page.
- **Reference content:** the techniques came from third-party reels. Nothing from them
  (design, text, assets) is in the code or the demo.

## Motion design (branch `derFlok/motion-design`)

The spike's verdict was "too plain, too little motion". This step builds the look from
the brand profile and a component set at the reference level (techniques only).

**Brand → motion style.** The profile's `motion` section gains `theme`, `depth`, `radius`,
`energy` and `type_scale` (`brand.py` validates them and derives missing ones:
`motion_style()`). The page runtime resolves them into `vc.style` and `--vc-*` properties.
A dark theme on a light brand uses `colors.dark`/`dark_text`, or text and surface swapped;
glow is the accent in layered shadows; a contrast guard moves any colour that misses its
minimum (ink 7:1, accent text and text on the accent 4.5:1, muted 3:1) toward black or white.
Energy scales durations (calm 1.2x, punchy 0.8x), spring overshoot and the micro-event beat
(0.5 / 0.4 / 0.3 s). `brand sheet` appends a motion row so the person sees their style.

**Components.** 23 in `renderer/motion/CATALOGUE.md`. Shared vocabulary: entrance with blur +
scale + spring, hand-over morph (`after`: the old element flies into the new one's box and
dissolves), micro-events in every gap longer than one beat (bump on an item already on screen,
sheen over the card), fit-to-band slots (components have base sizes; the slot scales them to
the band), heavy brand weights and big numbers. 3D: Three.js 0.186.1 (MIT) point field and
extruded number (the brand font drawn into a mask, stacked into a solid), rendered in `onSeek`.
The tilted UI plane uses CSS 3D instead of a WebGL texture: the text stays sharp, and DOM
content cannot be a WebGL texture without rasterising it first.

**Cues.** `vc.cue(kind, t, {component, strength})`; `motion.js --events <file>` writes them
sorted, in output seconds (`offset` in motion.json). The demo span gives 34 cues.

**Demo.** One scene, three looks (`_example`, a synthetic dark brand, a local copy of a real
brand), on a copy of the spike job: place card with a 3D point field behind it, chips with
icons per spoken item, a count to the spoken number, a comparison that is decided on a word,
two steps, the place card returning. Nothing repeats the caption words. Three render-inspect
rounds: (1) everything too small for the band, a beat on the wrong word occurrence; (2) a
button over the title at a fixed card width, steps showing only lit nodes; (3) clean.
Freezedetect on the motion clip: no still stretch ≥ 0.4 s at −60 dB (spike: 1.07 s), longest
0.6 s at −50 dB (spike: 1.63 s).

**Numbers** (Apple M3 Pro, 1080×1920, 30 fps, 20.7 s = 621 frames, `--region-only`
1080×536, two runs each, peak memory = RSS of the whole process tree sampled every 0.1 s):

| Scene | WebGL | Render | ms / frame | Peak memory |
|---|---|---|---|---|
| 2D (demo without the field) | SwiftShader | 44.6–45.6 s | 70–72 | 0.75–0.84 GB |
| 2D | Metal | 52.7–54.8 s | 84–85 | 0.67–0.71 GB |
| 3D (point field over the whole span) | SwiftShader | 63.1–79.1 s | 100–125 | 0.86–0.96 GB |
| 3D | Metal | 50.7–53.9 s | 80–84 | 0.66–0.81 GB |

2D costs twice the spike's frame (35 ms): the glow and soft shadows are large blurred layers
that are rastered again every frame while the drift scales them. Trying to hide transparent
elements and drop settled `blur(0px)` filters gave no gain.

**Determinism.** The DOM state is a pure function of t (hash of the stage per frame:
identical in two runs). The pixels are no longer bit-identical in long glow-heavy spans:

| Path | Frames that differ between two runs | Lowest PSNR |
|---|---|---|
| 2D SwiftShader | 50 / 621 | 46.7 dB |
| 3D SwiftShader | 103 / 621 | 57.5 dB |
| 2D Metal | 96 / 621 | 26.8 dB |
| 3D Metal | 112 / 621 | 30.3 dB |

The differences sit in entrance frames of glowing cards (big blurred shadows mid-blur). They
stay with `--run-all-compositor-stages-before-draw`, one raster thread, partial raster off and
with software compositing (`--disable-gpu`), so they come from Chromium's raster of large blur
layers, not from scene state. The short gallery and 3D tests are still bit-identical.
**Default: SwiftShader** — the same path on every machine and in CI, differences far below
visibility (≥ 46 dB), and faster for 2D; Metal is faster only while 3D runs, and its
differences can reach visible levels (27 dB). Verify should compare motion clips as "visually
identical" (PSNR ≥ 40 dB), not by hash; frame-exact output would need
`HeadlessExperimental.beginFrame` (deterministic begin-frame control), which is not built.
