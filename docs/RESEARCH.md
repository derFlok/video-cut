# Research summary (October 2026)

Condensed findings behind the design in `BRIEF.md`. Prices and versions are as of October 2026.

## Claude sees images, not video

- Claude accepts images only (JPEG/PNG/GIF/WebP; animated GIFs: first frame). With more than 20
  images in context, each side is limited to 2000 px.
  ([Claude Vision docs](https://platform.claude.com/docs/en/build-with-claude/vision))
- Image tokens ≈ ⌈width/28⌉ × ⌈height/28⌉. A 1080×1920 frame costs about 2,700 tokens. A 90-s clip
  sampled at 2 fps costs ~484k tokens at full size, ~54k at 360×640.
- In an agent session, every image read stays in context and is re-sent on later turns. Large image
  batches therefore belong in sub-agents that return text only.
- Consequence: transcript first, contact sheets (3×3 tiles of 360×640 with burned-in timecode) and
  burst sheets (±0.5 s at 4–6 fps) only at decision points. Pixel measurements (face centre,
  blinks, gaze) come from MediaPipe, not from Claude.

## Reference implementation

[browser-use/video-use](https://github.com/browser-use/video-use) (MIT) is a Claude Code skill that
already does: word-level transcript of all takes (~12 KB packed), `timeline_view` (filmstrip +
waveform + word labels) on demand, edit decision list, FFmpeg render, and a self-check at every cut
with up to three re-renders. It requires strategy approval before cutting. Its transcription uses
ElevenLabs Scribe (US by default; EU residency only on Enterprise), which this project replaces
with local models.

## Building blocks

| Block | Choice | Notes |
|---|---|---|
| Transcription | mlx-whisper (`word_timestamps`), parakeet-mlx (Parakeet TDT v3) | both local on Apple Silicon. CrisperWhisper tags fillers best (disfluency F1 87.8 vs 79.2 for Scribe v2) but its weights are non-commercial: comparison only |
| Silence | transcript gaps, optionally auto-editor, FFmpeg `silencedetect` | |
| Fillers, retakes | Claude on the transcript; cuts at word/pause boundaries with 30 ms fades | retake detection is undocumented anywhere; last complete take wins |
| Reframing + zooms | MediaPipe Face Landmarker → smoothed centre → FFmpeg `crop`/`scale` | Google AutoFlip unsupported since 03/2023. Zoom levels ~1.25× / 1.6× on emphasis |
| Captions, graphics | own canvas renderer (deterministic `draw(t)`, headless Chromium → FFmpeg) or HyperFrames (Apache-2.0) | Remotion costs ≥ USD 100/month for companies of 4+ people |
| Audio | DeepFilterNet → FFmpeg `loudnorm` 2-pass, −14 LUFS / −1 dBTP | |
| Music | burned in; Pixabay Music or Freesound CC0; `sidechaincompress` ducking | Instagram business accounts may only use the Meta Sound Collection. Pixabay tracks may not be redistributed as files, so they are never bundled |
| Stock B-roll | Pexels API (`orientation=portrait`, attribution link required) | |

Risk: Whisper tends to drop filler words; Deepgram's filler detection only covers English. A
fallback heuristic marks gaps between word timestamps that still contain voice energy as filler
candidates for Claude to judge on a burst sheet. Untested.

## Platform requirements (Instagram Reels)

- 1080×1920, MP4, H.264 + AAC ≥ 128 kbps, 30 fps, up to 3 minutes.
- Safe zones: organic ~250 px top / ~350 px bottom; Meta ads 14 % top, 35 % bottom, 6 % sides.
- Cover 1080×1920 with the title inside the centre 3:4 crop (profile grid).
- Ranking signals named by Instagram: watch time, likes per reach, sends per reach. 45–60 s was the
  strongest band in an analysis of ~140k business reels (Buffer).
- Hook in the first two seconds; call to action aimed at sharing.

## Law (EU)

- AI Act Art. 50(4), in force since 2 August 2026: deepfakes (AI-generated or manipulated content
  resembling real people, places or events that would falsely appear authentic) must be disclosed.
  Cutting, captions, denoising, reframing and stylised graphics are not covered. Photorealistic
  AI B-roll, cloned voices and avatars are, and must be labelled visibly.
- Other people in frame need consent; employees need separate written, revocable consent (German
  § 26(2) BDSG). Never film customer data on screen.
- A voice or avatar clone of a real person may be biometric data (Art. 9 GDPR).

## Cost

Local steps cost nothing. Claude tokens per short video, estimated: contact sheets ~54k, two check
rounds ~55k, output 10–30k, so roughly USD 0.50–1.50. Runtime estimated at 5–15 minutes unattended.
Neither figure has been measured yet.

## Main open questions

1. Do local models keep German filler words?
2. Can Claude judge takes and facial expressions on 360-px tiles?
3. How much taste survives without human approval?

All three are to be measured on real recordings before the pipeline is extended.
