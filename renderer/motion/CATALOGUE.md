# Motion component catalogue

The overlay skill reads this file to choose what a motion span shows. Components live in
`renderer/motion/components.js` and are called from a scene module as `vc.c.<name>(parent, props)`.
Every component returns its root element, puts its tweens on the scene timeline, emits cues
for the sound track (`events.json`) and takes its whole look from the brand profile the
person selected for the video.

## Rules for choosing

1. **Show what is meant, never what is said.** The captions already carry the words. The band
   shows the number, the list, the picture, the UI, the chart or the decision. A `headline`
   is only for a short claim or label the speaker does not say word for word.
2. **One component in the band at a time.** The next one takes over with `after: <previous root>`
   (it morphs out of the previous one). A plain `out` blur-out is the exception.
3. **Something changes at least every `vc.style.beat` seconds** (0.5 calm, 0.4 normal,
   0.3 punchy). Components fill the gaps between their own events with micro-events
   (bumps on items already on screen, a sheen over cards). The scene still has to give each
   component word-synced events: an item per spoken item, a count on the spoken number.
4. **Put every component in its own `c.slot(stage)`.** A slot scales its content to fill the
   band (`fit`, at most 1.8x), so sizes in props are base sizes, not final pixels. A 3D
   background goes in `c.slot(stage, { fit: false })` under the slot of the card in front of it.
5. **3D is an option, not a default.** Use `field3d` behind an opening card or `number3d` for
   one hero number. Each 3D span costs about twice the frame time of a 2D span on SwiftShader
   (see Numbers).

## Common props

| Prop | Meaning |
|---|---|
| `at` | when it enters (scene seconds); most components derive it from their first item |
| `out` | when it leaves (blur out, `swoosh_out` cue); leave it out when the next one takes over |
| `after` | element on screen that morphs into this one at `at` (hand-over, `whoosh` cue) |
| `enter` | entry time when it differs from the first item's time (stat, checklist, steps, ...) |
| `micro` | `false` switches micro-events off |
| `width` | base width as a share of the unit (frame short side) |

Times come from `vc.at("word", n)`, `vc.end("word", n)` or `vc.beat("id")`.

## Cues

`vc.cue(kind, t, {component, strength})` — kinds: `pop`, `whoosh`, `tick`, `check`, `strike`,
`type`, `count`, `impact`, `swoosh_out`. `t` is scene seconds; the renderer writes
`[{t, kind, component, strength}]` with `t` in output-video seconds (scene t + `offset` from
motion.json) to the `--events` file, sorted by t. Micro-events emit no cue (they would make
the sound track busy); entries, items, checks, counts and landings do.

## Components

Durations: **min** is the shortest span that reads (entry + content + a beat of rest);
**max** is where it starts to feel parked (then hand over to the next component).

| Component | Shows | Required props | Optional props | Cues | Min / max |
|---|---|---|---|---|---|
| `headline` | a short claim, words building one by one; a word boxed, underlined or lit | `words: [{text, at}]` | `mark: [{index, at, style: box\|underline\|glow}]`, `size` (0.085), `card` (true) | `pop` per word (0.2), `impact` per mark | 1.2 / 4 s |
| `stat` | a big number counting (odometer reels for whole numbers) with unit, label, source; the unit can change on a word | `value`, `at` (count start) | `from` (0), `dur` (0.9), `decimals`, `sep`, `dec`, `prefix`, `unit`, `unitTo` + `unitAt`, `label`, `source`, `bar` (0..1), `size` (0.22) | `pop`, `count`, `tick`s, `impact`; `whoosh` on unit change | 1.5 / 5 s |
| `counter` | spike alias of `stat` (`to`, `suffix`) | `to`, `at` | as `stat` | as `stat` | 1.5 / 5 s |
| `checklist` | items appearing and being checked, a tally pill 0/n ... n/n | `items: [{text, at, check?}]` | `label`, `columns`, `size` (0.05), `tally` (true) | `pop` per item, `check` per check | 2 / 10 s |
| `strikeCard` | an option struck through, the answer popping in | `text`, `strikeAt` | `label`, `then`, `thenAt`, `size` | `pop`, `strike`, `impact` | 1.5 / 4 s |
| `compare` | two columns, one wins (loser dims and is struck, winner grows with a check badge) | `left`, `right` (`{title, items?, value?, icon?}`), `at` | `winner` (`right`), `winAt`, `vs` (text or false), `width` (0.84) | `pop` per side, `strike`, `impact` | 2 / 6 s |
| `steps` | a timeline: nodes light up, the track draws between them | `steps: [{title, at, sub?, icon?}]` | `width` (0.86), `enter` | `pop`, `tick` per step, `check` on the last | 2 / 8 s |
| `table` | header, then rows sliding in on their words; `hi` rows light up | `columns: [text \| {text, num}]`, `rows: [{cells, at, hi?}]` | `title`, `width` (0.84) | `pop`, `tick` per row, `impact` on `hi` | 2 / 8 s |
| `barChart` | bars growing on their words, values counting along, a winner lighting up | `bars: [{label, value, at}]` | `unit`, `max`, `decimals`, `title`, `highlight` + `highlightAt`, `height` (0.24), `width` (0.78) | `pop` per bar (strength by value), `impact` | 2 / 6 s |
| `lineChart` | a line drawing point to point (area under it), the head glowing, the value in a pill | `points: [{v, at}]` | `unit`, `decimals`, `area` (true), `title`, `height`, `width` | `pop`, `tick` per point, `impact` on the last | 2 / 6 s |
| `meter` | progress bar or ring filling to a value, the number counting | `value`, `at` | `max` (100), `dur` (1), `kind: bar\|ring`, `label`, `unit` (`%`), `decimals`, `width` | `pop`, `count`, `tick`s, `impact` | 1.5 / 4 s |
| `chips` | tags with icons popping in one by one, all pulsing together on `allAt` | `chips: [{text, at, icon?}]` | `allAt`, `width` | `pop` per chip, `impact` on `allAt` | 1.5 / 8 s |
| `quote` | a quote mark and words lighting up on their times (or all at once), attribution | `words: [{text, at, mark?}]` or `text` | `by`, `width` | `whoosh`, `impact` per marked word | 2 / 6 s |
| `pointer` | an arrow (default down, toward the speaker) bouncing on the beat, with a label | `at` | `dir: down\|up\|left\|right`, `label`, `size` | `whoosh` | 1 / 4 s |
| `imageFrame` | an approved material still (file in the job folder) in a frame with parallax (picture pans and zooms, frame tilts, caption moves at another rate) | `src`, `at` | `width` (0.6), `height` (0.34), `caption`, `pan: [dx, dy]` | `whoosh`, `pop` | 2 / 6 s |
| `frame` | a UI rebuilt from parts: `browser` (dots + address), `window` (dots + title), `phone` (device frame), `terminal`; `root.body` holds content | — | `kind`, `url`, `title`, `width`, `height`, `at` | `pop` | 2 / 8 s |
| `terminal` | lines typed character by character, output lines at once, glowing caret | `lines: [{text, at, cps?, out?, prompt?}]` | `width` (0.84), `title` | `pop`, `type` per typed line, `tick` per output line | 2 / 8 s |
| `chat` | message bubbles (in / out) with typing dots before each | `messages: [{text, at, side, typing?}]` | `width`, `at` (standalone) | `pop` per message | 2 / 8 s |
| `fileList` | file browser rows (file / folder icons, meta, highlight) | `files: [{name, at, kind, indent?, meta?, hi?}]` | `width`, `at` (standalone) | `tick` per row | 2 / 8 s |
| `appCard` | an app / place card: icon tile, title, star rating filling, meta, a button pressed | `title`, `at` | `icon`, `meta`, `rating` + `stars` + `ratingAt`, `action` + `actionAt`, `width` | `pop`, `tick` per star and press | 1.5 / 6 s |
| `tilt` | any element on a tilted plane with a camera move (CSS 3D, text stays sharp) | element, `at` | `from` / `to` `{rx, ry, rz, z}`, `until` | — | 1.5 / 8 s |
| `field3d` | **3D** (Three.js): a glowing point floor (`grid`) or cloud (`particles`) receding into fog, camera gliding forward; behind a card | `at` | `out`, `kind`, `count`, `speed` | `whoosh` | 1 / 20 s |
| `number3d` | **3D** (Three.js): a number or short word in the brand font as an extruded solid swinging in and orbiting | `text`, `at` | `out`, `size` (1), `depth` (0.35) | `impact`, `swoosh_out` | 1.2 / 4 s |

UI frames are generic (made-up URL, no product names, no logos). Content inside them is the
speaker's subject, never a real product's screen.

## Vocabulary for custom elements

`c.pop(el, t, {dir, scale, blur, cue})`, `c.leave(el, t)`, `c.morph(a, b, t)`, `c.pulse(el, t, k, {cue})`,
`c.nudge(el, t)`, `c.drift(el, t0, t1)`, `c.sheen(card, t)`, `c.micro(root, t0, t1, eventTimes, targets)`,
`c.setAt(el, prop, value, t)`, `c.slot(stage, {fit, max, row, gap})`, `c.icon(name, parent, size)`.

**Icons** (`c.ICONS`): check, x, plus, minus, arrow-right, arrow-down, arrow-up, trend-up,
trend-down, star, heart, bolt, clock, calendar, user, users, home, pin, globe, sun, wave,
mountain, bike, flag, paddle, file, folder, chart, search, gear, chat, lock, play, euro,
percent, spark, quote. Drawn for this project (Apache-2.0, no third-party artwork), stroked in
`currentColor`, so they take the brand colours.

## Brand → look (interface 1)

The brand profile's `motion` section: `theme` (dark | light | auto from the background
luminance), `depth` (flat | soft | glow; default glow on dark, soft on light), `radius` (share of
the unit, 0–0.12, default 0.028), `energy` (calm | normal | punchy; default from
`motion.character`), `type_scale` (0.6–1.6). The runtime resolves them into `vc.style` and
CSS custom properties components use: `--vc-panel`, `--vc-panel-2`, `--vc-ink`, `--vc-ink-muted`,
`--vc-line`, `--vc-hi` (accent fill), `--vc-on-hi`, `--vc-hi-text` (accent as text, ≥ 4.5:1 on the
panel), `--vc-hi-soft`, `--vc-glow`, `--vc-glow-soft`, `--vc-shadow`, `--vc-shadow-lg`,
`--vc-text-glow`, `--vc-radius`, `--vc-radius-sm`, `--vc-ts`, `--vc-sheen` (plus the spike's
`--vc-bg/surface/text/muted/accent/...`). A dark theme on a light brand uses the brand's
`colors.dark`/`dark_text`, or text and surface swapped. Contrast guard: ink ≥ 7:1 on the panel,
accent text ≥ 4.5:1, text on the accent ≥ 4.5:1, muted ≥ 3:1 (a colour that misses is moved
toward black or white until it passes). Energy scales durations (calm 1.2x, punchy 0.8x),
spring overshoot and the micro-event beat.

## Numbers

See `docs/spikes/motion.md` (Motion design) for render cost per 20 s span, 2D vs 3D,
SwiftShader vs Metal, and the determinism check.
