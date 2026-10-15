"""`videocut tool transcribe|candidates`: local transcription and filler candidates.

transcribe [--model turbo|parakeet] [--language de|en|...|auto] [--no-align]
    work/audio.wav -> work/words.json  {"model", "language", "duration",
                                        "words": [{"w", "t0", "t1", "conf",
                                                   "t0_asr", "t1_asr"}]}  (source s)
    then aligns every word against the audio (`align.py`: t0/t1 aligned, the model's own
    times kept in t0_asr/t1_asr; `--no-align` keeps the model's times) and runs
    `candidates` (cheap) so one call leaves a complete takes_packed.md.
    A words.json newer than audio.wav from the same model and language is kept
    (work/transcribe.json records how it was made, "align" how it was aligned, and
    "time_base": "video" when audio.wav is in video time, see ingest.py).
candidates
    work/words.json + work/audio.wav -> work/candidates.json
        {"pauses": [{"t0", "t1", "dur"}],
         "fillers": [{"t0", "t1", "kind": "word"|"gap_energy", "text", "score"}]}
    and rewrites work/takes_packed.md with filler marks.

Models live in $VIDEOCUT_HOME/models/<manifest name> (`videocut models pull <name>`).
turbo = mlx-whisper large-v3-turbo; it is prompted with a disfluent German/English sample
so it keeps more "äh"/"ähm" (measured; see the PR that added this). parakeet = parakeet-mlx
TDT 0.6b v3 (detects the language itself). Recordings never leave the machine.

Vocabulary (`vocab.py`): turbo's prompt also names the canonical terms of the job's
effective vocabulary (after the filler sample, within Whisper's prompt budget), and after
transcription and alignment every run applies the vocabulary to words.json (variants ->
canonical spelling, the model's text kept in "w_asr"); transcribe.json "vocabulary" has the
counts and "prompt_terms" the terms the prompt named. A changed vocabulary does not
transcribe again: it is applied to the kept transcript.
"""

import json
import os
import time
import wave
from pathlib import Path

from . import _transcribe_energy as energy
from . import align as aligner
from . import vocab
from ._transcribe_pack import pack
from .common import ToolError, home, is_fresh, job_parent, resolve_job, run_tool, write_json

MODELS = {
    "turbo": {"name": "whisper-large-v3-turbo", "files": ["config.json", "weights.safetensors"]},
    "parakeet": {"name": "parakeet-tdt-0.6b-v3", "files": ["config.json", "model.safetensors"]},
}
# Disfluent sample text as initial prompt: Whisper imitates the style of its prompt, so
# fillers in the prompt make it transcribe fillers instead of silently dropping them.
FILLER_PROMPT = {
    "de": "Ähm, also, äh, ich wollte, ähm, kurz was sagen. Äh, ja, hm.",
    "en": "Um, so, uh, I wanted to, um, say something. Uh, yeah, hm.",
}


def model_dir(key: str) -> Path:
    m = MODELS[key]
    d = home() / "models" / m["name"]
    if not all((d / f).is_file() for f in m["files"]):
        raise ToolError(
            f"model {m['name']} is not installed in {d.parent}; "
            f"run `videocut models pull {m['name']}`"
        )
    return d


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as f:
        return f.getnframes() / f.getframerate()


def _clean(raw: list[tuple[str, float, float, float | None]], duration: float) -> list[dict]:
    """Strip text, drop empty tokens, keep times monotonic and inside the recording."""
    words, last = [], 0.0
    for text, t0, t1, conf in raw:
        text = text.strip()
        if not text:
            continue
        t0 = min(max(float(t0), last), duration)
        t1 = min(max(float(t1), t0), duration)
        last = t0
        words.append(
            {
                "w": text,
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "conf": None if conf is None else round(float(conf), 3),
            }
        )
    return words


def _mlx(module: str):
    """Import an MLX package; a clear error where MLX cannot run (it needs Apple silicon)."""
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as e:
        raise ToolError(
            f"{module} cannot load here ({e}); transcription needs Apple silicon"
        ) from None


def prompts(language: str | None, entries: list[dict], prompt: bool = True) -> list[tuple]:
    """Initial prompts to try in order, as (text, terms): the filler sample with the
    vocabulary's terms, then the filler sample alone, then none (when decoding falls back
    to sampling, the next one is tried)."""
    if not prompt:
        return [(None, [])]
    filler = FILLER_PROMPT.get(language or "")
    full, terms, _ = vocab.prompt_text(filler, entries)
    out = [(full, terms)]
    if terms and filler:
        out.append((filler, []))
    if out[-1][0] is not None:
        out.append((None, []))
    return out


def run_whisper(
    audio: Path, language: str | None, prompt: bool = True, entries: list[dict] | None = None
) -> tuple[list, str, bool, list[str]]:
    """Words from mlx-whisper; the bool says whether the filler prompt was kept, the list
    which vocabulary terms the kept prompt named."""
    d = model_dir("turbo")
    mlx_whisper = _mlx("mlx_whisper")

    tries = prompts(language, entries or [], prompt)
    while True:
        text, terms = tries.pop(0)
        result = mlx_whisper.transcribe(
            str(audio),
            path_or_hf_repo=str(d),
            word_timestamps=True,
            language=language,
            initial_prompt=text,
            # Measured: with the prompt, plain decoding sometimes falls back to sampling
            # (temperature > 0) and repeats itself; skipping silent hallucinations keeps it
            # on greedy decoding.
            hallucination_silence_threshold=2.0,
            verbose=None,
        )
        segments = result.get("segments", [])
        if tries and any(seg.get("temperature", 0.0) > 0.0 for seg in segments):
            what = "without the vocabulary terms" if tries[0][0] else "without it"
            print(f"the prompt made decoding unstable; transcribing again {what}")
            continue
        break
    raw = [
        (w["word"], w["start"], w["end"], w.get("probability"))
        for seg in segments
        for w in seg.get("words", [])
    ]
    filler_kept = text is not None and text.startswith(FILLER_PROMPT.get(language or "", "\0"))
    return raw, result.get("language") or language or "unknown", filler_kept, terms


def run_parakeet(audio: Path) -> tuple[list, str]:
    d = model_dir("parakeet")
    os.environ["HF_HUB_OFFLINE"] = "1"  # load from the local folder, never the network
    model = _mlx("parakeet_mlx").from_pretrained(str(d))
    duration = wav_duration(audio)
    result = model.transcribe(str(audio), chunk_duration=120.0 if duration > 150 else None)
    # Tokens are sub-words; a token starting with a space begins a new word.
    raw: list[list] = []
    for tok in result.tokens:
        if raw and not tok.text.startswith(" "):
            raw[-1][0] += tok.text
            raw[-1][2] = tok.end
            confs = [c for c in (raw[-1][3], tok.confidence) if c is not None]
            raw[-1][3] = min(confs) if confs else None
        else:
            raw.append([tok.text, tok.start, tok.end, tok.confidence])
    return [tuple(r) for r in raw], "auto"


def _work(job: Path) -> Path:
    work = job / "work"
    if not (work / "audio.wav").is_file():
        raise ToolError("work/audio.wav is missing; run `videocut tool ingest` first")
    return work


def make_candidates(work: Path) -> dict:
    words_file = work / "words.json"
    if not words_file.is_file():
        raise ToolError("work/words.json is missing; run `videocut tool transcribe` first")
    doc = json.loads(words_file.read_text())
    x, rate = energy.read_wav(work / "audio.wav")
    frames = energy.Frames(x, rate)
    duration = doc.get("duration") or len(x) / rate
    cands = energy.find(doc["words"], frames, duration, doc.get("language"))
    write_json(work / "candidates.json", cands)
    packed = pack(doc, cands)
    tmp = work / "takes_packed.md.tmp"
    tmp.write_text(packed)
    tmp.replace(work / "takes_packed.md")
    kinds = [f["kind"] for f in cands["fillers"]]
    return {
        "pauses": len(cands["pauses"]),
        "filler_words": kinds.count("word"),
        "gap_energy": kinds.count("gap_energy"),
        "noise_db": round(frames.noise, 1),
        "packed_kb": round(len(packed.encode()) / 1024, 1),
    }


def sync_alignment(work: Path, meta: dict, wanted: bool) -> dict | None:
    """Bring words.json to the wanted alignment state; returns the "align" record."""
    current = meta.get("align") or {}
    if wanted:
        if (current.get("method"), current.get("version")) == (aligner.METHOD, aligner.VERSION):
            return current
        info = aligner.align_work(work)
        print(f"aligned words to the audio: median shift {info['median_shift_ms']} ms")
        return info
    words_file = work / "words.json"
    doc = json.loads(words_file.read_text())
    if current or any("t0_asr" in w for w in doc["words"]):
        doc["words"] = aligner.strip(doc["words"])
        write_json(words_file, doc)
        meta.pop("align", None)
        write_json(work / "transcribe.json", meta)
    return None


def audio_time_base(work: Path) -> str:
    """ "video" when ingest put audio.wav in video time, "audio" for an older job."""
    try:
        return json.loads((work / "audio.json").read_text()).get("time_base") or "audio"
    except (OSError, ValueError):
        return "audio"


def shift_to_video_time(work: Path, offset: float) -> None:
    """Move an existing transcript by `offset` s into video time (ingest's upgrade of a
    job transcribed before the video time base), instead of transcribing again: the words
    under an approved plan stay the same. Re-aligns and redoes candidates when there."""
    words_file, meta_file = work / "words.json", work / "transcribe.json"
    doc, meta = json.loads(words_file.read_text()), json.loads(meta_file.read_text())
    duration = round(wav_duration(work / "audio.wav"), 3)
    words = []
    for w in doc["words"]:
        w = dict(w)
        for k in ("t0", "t1", "t0_asr", "t1_asr"):
            if k in w:
                w[k] = round(min(max(w[k] + offset, 0.0), duration), 3)
        if w["t1"] > 0.0 or offset >= 0:  # words trimmed away with the audio are gone
            words.append(w)
    doc.update(words=words, duration=duration)
    write_json(words_file, doc)
    meta.update(time_base="video", shifted_s=offset)
    meta.pop("align", None)
    write_json(meta_file, meta)
    if any("t0_asr" in w for w in words):
        aligner.align_work(work)
    make_candidates(work)


def transcribe(args) -> dict:
    job = resolve_job(args.job)
    work = _work(job)
    audio, words_file, meta_file = work / "audio.wav", work / "words.json", work / "transcribe.json"
    language = None if args.language == "auto" else args.language
    words = vocab.effective(job)
    meta = {
        "model": MODELS[args.model]["name"],
        "language": "auto" if args.model == "parakeet" else args.language,
        "filler_prompt": args.model == "turbo",
    }
    try:
        prev = json.loads(meta_file.read_text()) if is_fresh([meta_file], [audio]) else {}
    except (OSError, ValueError):
        prev = {}
    fresh = is_fresh([words_file], [audio]) and {k: prev.get(k) for k in meta} == meta
    if not fresh:
        meta_file.unlink(missing_ok=True)  # written last: marks words.json as complete
        started = time.monotonic()
        terms: list[str] = []
        if args.model == "turbo":
            raw, detected, prompt_used, terms = run_whisper(audio, language, entries=words)
        else:
            (raw, detected), prompt_used = run_parakeet(audio), False
        duration = round(wav_duration(audio), 3)
        doc = {
            "model": meta["model"],
            "language": detected,
            "duration": duration,
            "words": _clean(raw, duration),
        }
        write_json(words_file, doc)
        meta["seconds"] = round(time.monotonic() - started, 1)
        meta["prompt_used"] = prompt_used
        meta["prompt_terms"] = terms
        meta["time_base"] = audio_time_base(work)
        write_json(meta_file, meta)
        print(f"transcribed {duration:.1f}s with {meta['model']} in {meta['seconds']}s")
    else:
        meta = prev
    aligned = sync_alignment(work, meta, not args.no_align)
    applied = vocab.apply_job(job, words, captions=False, candidates=False)
    doc = json.loads(words_file.read_text())
    stats = make_candidates(work)
    return {
        "job": str(job),
        "transcribed": not fresh,
        "model": doc["model"],
        "language": doc["language"],
        "words": len(doc["words"]),
        "seconds": meta.get("seconds"),
        "filler_prompt": meta.get("prompt_used", False),
        "aligned": aligned is not None,
        "align_shift_ms": aligned and aligned["median_shift_ms"],
        "vocabulary": {k: applied[k] for k in ("applied", "terms")},
        **stats,
    }


def candidates(args) -> dict:
    job = resolve_job(args.job)
    return {"job": str(job), **make_candidates(_work(job))}


def register(subparsers) -> None:
    t = subparsers.add_parser(
        "transcribe", help="local transcription with word timestamps", parents=[job_parent()]
    )
    t.add_argument("--model", choices=["turbo", "parakeet"], default="turbo")
    t.add_argument(
        "--language",
        default="de",
        help="spoken language code for turbo (default de); `auto` detects it",
    )
    t.add_argument(
        "--no-align",
        action="store_true",
        help="keep the model's word times (default: align them to the audio)",
    )
    t.set_defaults(handler=run_tool("transcribe", transcribe))
    c = subparsers.add_parser(
        "candidates", help="filler-word candidates between words", parents=[job_parent()]
    )
    c.set_defaults(handler=run_tool("candidates", candidates))
