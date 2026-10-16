"""The human's word edits of the transcript: `videocut tool transcript show|edit|undo`.

An edit changes the TEXT of one word of work/words.json, never its timing:
    {"w": "<new text>", "w_asr": "<the model's text>", "edit": "<new text>", ...}
- "w_asr" follows the vocabulary's convention (tools/vocab.py): the model's own text;
- "edit" marks the human's text; a word with it is never touched by the vocabulary
  (`vocab apply` restores and re-corrects every other word, an edited one keeps its text,
  so the manual edit wins and survives every apply);
- an empty edit hides the word from the captions: "hidden": true, "w" stays the model's
  text and the word stays in the timing data (cuts, fillers, alignment);
- text with spaces stays ONE timed token; the captions show the text as written.

Every change is logged as {at, i, t0, asr, from, to, prev} (`undo: true` on an undo) in
work/transcript_edits.jsonl and, as the record that counts, in
$VIDEOCUT_HOME/state/<job>/transcript_edits.jsonl, outside what an agent run may write.
`enforce` replays that record onto words.json (run by every edit, undo, `vocab apply`,
`transcribe` and the caption refresh), so an agent run cannot rewrite the human's edits
silently; `show` reports a word that differs from the record as drift.

Captions: `refresh(job)` (the app's "Untertitel neu setzen", `POST /captions/refresh`) puts
the edited text into the EDL's caption blocks (hidden words dropped; a caption that cannot
be matched by time, or a hidden word to bring back, rebuilds them with `captions --force`),
then `overlays`, `render` and `verify` run as tool steps, no Claude run.

    transcript show <job> [--from t --to t]   words with their index and source time
    transcript edit <job> <i> <text>          set the text of word i ("" hides it)
    transcript undo <job>                     undo the last edit
"""

import json
import os
import unicodedata
from datetime import datetime
from pathlib import Path

from ..paths import state_dir
from .common import ToolError, job_parent, read_job, resolve_job, run_tool, update_job, write_json

MAX_TEXT = 60
LOG = "transcript_edits.jsonl"
FAR_S = 3.0  # an edit finds its word again: the same text, the nearest start this close
JOINERS = {"\u200c", "\u200d"}  # zero-width (non-)joiner: needed in some scripts, emoji
CAPTION_S = 0.002  # a caption word is the transcript word with the same output start


# --- text and words -------------------------------------------------------------------


def clean_text(text) -> str:
    """The edit text, validated: a string of at most MAX_TEXT characters without control
    characters; runs of whitespace become one space. "" hides the word."""
    if not isinstance(text, str):
        raise ToolError("the text must be a string")
    if any(
        unicodedata.category(c) in ("Cc", "Cs", "Co")
        or (unicodedata.category(c) == "Cf" and c not in JOINERS)
        for c in text
    ):
        raise ToolError("the text must not contain control or formatting characters")
    text = " ".join(text.split())
    if len(text) > MAX_TEXT:
        raise ToolError(f"the text may have at most {MAX_TEXT} characters")
    return text


def asr(w: dict) -> str:
    """The model's text of a word."""
    return w.get("w_asr", w["w"])


def set_edit(w: dict, text: str) -> None:
    w.setdefault("w_asr", w["w"])
    w.pop("vocab", None)
    w["edit"] = text
    if text:
        w["w"] = text
        w.pop("hidden", None)
    else:
        w["w"] = w["w_asr"]
        w["hidden"] = True


def clear_edit(w: dict) -> None:
    """The word as the model wrote it again (the vocabulary corrects it afterwards)."""
    if "edit" not in w and "hidden" not in w:
        return
    w.pop("edit", None)
    w.pop("hidden", None)
    w["w"] = w["w_asr"] if "w_asr" in w else w["w"]
    if "parts" not in w:
        w.pop("w_asr", None)


def _key(w_or_text) -> str:
    from .vocab import key

    return key(w_or_text if isinstance(w_or_text, str) else asr(w_or_text))


def _words_doc(job: Path) -> dict:
    try:
        doc = json.loads((job / "work" / "words.json").read_text())
    except (OSError, ValueError):
        doc = None
    if not isinstance(doc, dict) or not isinstance(doc.get("words"), list):
        raise ToolError("work/words.json is missing; run `videocut tool transcribe` first")
    return doc


# --- the edit record ------------------------------------------------------------------


def record_path(job: Path) -> Path:
    return state_dir() / job.resolve().name / LOG


def read_log(job: Path) -> list[dict] | None:
    """The edit record, None when the human never edited this job folder. Only the state
    copy counts: the job's own copy is agent-writable and never read."""
    try:
        lines = record_path(job).read_text().splitlines()
    except OSError:
        return None
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and isinstance(e.get("t0"), int | float):
            out.append(e)
    return out


def _append(job: Path, entry: dict) -> str | None:
    """Add to the record and mirror it into the job's copy; returns the record before (to
    roll back), None when there was none."""
    rec, mirror = record_path(job), job / "work" / LOG
    before = rec.read_text() if rec.is_file() else None
    rec.parent.mkdir(parents=True, exist_ok=True)
    with rec.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    mirror.write_text(rec.read_text())
    return before


def _rollback(job: Path, before: str | None) -> None:
    rec, mirror = record_path(job), job / "work" / LOG
    if before is None:
        rec.unlink(missing_ok=True)
        mirror.unlink(missing_ok=True)
    else:
        rec.write_text(before)
        mirror.write_text(before)


def replay(entries: list[dict]) -> tuple[list[tuple[float, str, str | None]], list[dict]]:
    """(final edits [(t0, asr, text or None)], the stack of edits an undo can take back)."""
    state: dict[tuple, tuple[float, str, str | None]] = {}
    stack: list[dict] = []
    for e in entries:
        k = (round(float(e["t0"]), 3), _key(str(e.get("asr", ""))))
        if e.get("undo"):
            if stack:
                last = stack.pop()
                lk = (round(float(last["t0"]), 3), _key(str(last.get("asr", ""))))
                state[lk] = (last["t0"], str(last.get("asr", "")), last.get("prev"))
            continue
        stack.append(e)
        state[k] = (e["t0"], str(e.get("asr", "")), e.get("to"))
    return list(state.values()), stack


def find(words: list[dict], t0: float, asr_text: str) -> int | None:
    """Index of the word an edit was made on: the same model text, the nearest start."""
    k = _key(asr_text)
    best = None
    for i, w in enumerate(words):
        d = abs(w["t0"] - t0)
        if d <= FAR_S and _key(w) == k and (best is None or d < best[0]):
            best = (d, i)
    return best[1] if best else None


def enforce(job: Path, words: list[dict]) -> int:
    """Make the edit fields of `words` exactly the record (in place); returns how many
    words had to change (an edit lost or added outside the record). Without a record (the
    human never edited this folder) nothing changes."""
    log = read_log(job)
    if log is None:
        return 0
    final, _ = replay(log)
    want: dict[int, str] = {}
    for t0, a, text in final:
        i = find(words, t0, a)
        if i is not None and text is not None:
            want[i] = text
    changed = 0
    for i, w in enumerate(words):
        if i in want:
            if w.get("edit") != want[i] or w.get("w") != (want[i] or asr(w)):
                set_edit(w, want[i])
                changed += 1
        elif "edit" in w or "hidden" in w:
            clear_edit(w)
            changed += 1
    return changed


def drift(job: Path, words: list[dict]) -> int:
    """Words that differ from the edit record (0 when the record holds)."""
    import copy

    return enforce(job, copy.deepcopy(words))


# --- operations -----------------------------------------------------------------------


def _human_only() -> None:
    from .job import AGENT_ENV

    if os.environ.get(AGENT_ENV):
        raise ToolError("only a human edits the transcript (not available inside an agent run)")


def _save(job: Path, doc: dict) -> None:
    """words.json with the record enforced and the vocabulary applied around it, the packed
    transcript re-written, and the overlays marked stale when the captions change."""
    from . import vocab

    enforce(job, doc["words"])
    doc["words"] = vocab.apply_words(doc["words"], vocab.effective(job))
    work = job / "work"
    write_json(work / "words.json", doc)
    try:
        from ._transcribe_pack import pack

        cands = json.loads((work / "candidates.json").read_text())
        (work / "takes_packed.md").write_text(pack(doc, cands))
    except (OSError, ValueError):
        pass
    mark_stale(job)


def _commit(job: Path, entry: dict, doc: dict) -> None:
    """Record the entry and save words.json; a failed save takes the entry back."""
    before = _append(job, entry)
    try:
        _save(job, doc)
    except BaseException:
        _rollback(job, before)
        raise


def mark_stale(job: Path) -> bool:
    """A job with rendered captions: job.json overlays.stale = "transcript" (also over
    "vocabulary": the tool refresh sets those captions too). A job without captions just
    keeps the edit."""
    try:
        caps = json.loads((job / "work" / "edl.json").read_text()).get("captions")
    except (OSError, ValueError, AttributeError):
        caps = None
    rec = read_job(job).get("overlays")
    if not caps or not isinstance(rec, dict):
        return False
    if rec.get("stale") != "transcript":
        update_job(job, overlays={**rec, "stale": "transcript"})
    return True


def edit(job: Path, i: int, text: str, t0: float | None = None) -> dict:
    """Set the text of word i; `t0` (optional) must be its start, else the transcript
    changed since it was read."""
    _human_only()
    text = clean_text(text)
    doc = _words_doc(job)
    words = doc["words"]
    enforce(job, words)  # "prev" below comes from the record, not from what is on disk
    if not 0 <= i < len(words):
        raise ToolError(f"no word {i} (the transcript has {len(words)})")
    w = words[i]
    if t0 is not None and abs(w["t0"] - t0) > 1e-3:
        raise ToolError("the transcript changed since it was read; load it again")
    if ("edit" in w and w["edit"] == text) or (text and text == w["w"] and "edit" not in w):
        return {"i": i, "changed": False, "word": w}
    entry = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "i": i,
        "t0": w["t0"],
        "asr": asr(w),
        "from": "" if w.get("hidden") else w["w"],
        "to": text,
        "prev": w.get("edit"),
    }
    _commit(job, entry, doc)
    i2 = find(doc["words"], entry["t0"], entry["asr"])
    i = i if i2 is None else i2
    return {"i": i, "changed": True, "word": doc["words"][i]}


def undo(job: Path) -> dict:
    _human_only()
    _, stack = replay(read_log(job) or [])
    if not stack:
        raise ToolError("there is no edit to undo")
    last = stack[-1]
    doc = _words_doc(job)
    entry = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "undo": True,
        "i": last.get("i"),
        "t0": last["t0"],
        "asr": last.get("asr", ""),
        "from": last.get("to"),
        "to": last.get("from"),
        "prev": last.get("to"),
    }
    _commit(job, entry, doc)
    i = find(doc["words"], last["t0"], str(last.get("asr", "")))
    return {"i": i, "undone": {k: last.get(k) for k in ("i", "from", "to")}}


def edits_at(job: Path) -> str | None:
    """When the last edit (or undo) was made (ISO), None without one."""
    entries = read_log(job)
    return entries[-1].get("at") if entries else None


def after_agent(job: Path, say=print) -> None:
    """After an agent run: the human's edits back into words.json if the run changed them
    (`run.claude`)."""
    try:
        doc = _words_doc(job)
        if n := enforce(job, doc["words"]):
            _save(job, doc)
            say(f"  {n} word(s) of the human's transcript edits restored")
    except (ToolError, OSError, ValueError, KeyError, TypeError) as e:  # never ends a run
        say(f"  the transcript edits could not be checked: {e}")


def stale_if_drift(job: Path, say=print) -> None:
    """After the overlays were recorded: captions that do not show the human's text make
    them stale again ("Untertitel neu setzen" offers the tool refresh)."""
    try:
        n = caption_drift(job)
        if n:
            mark_stale(job)
            say(f"  {n} caption word(s) do not show the transcript edits; set them again")
    except (ToolError, OSError, ValueError, KeyError, TypeError) as e:  # best effort
        say(f"  the captions could not be checked against the transcript edits: {e}")


def caption_drift(job: Path) -> int:
    """Caption words of the EDL that do not show the human's text (0 without edits): an
    agent run that rebuilt or changed the captions makes the overlays stale again."""
    if read_log(job) is None:
        return 0
    res = patch_captions(job, write=False)
    return res["changed"] + (1 if res["rebuilt"] else 0)


# --- output time ----------------------------------------------------------------------


def output_times(words: list[dict], edl_data: dict | None) -> list[tuple | None]:
    """Per word its (t0, t1) in the reel when the cut keeps it (the captions' own rule,
    captions.map_words), else None."""
    from ..edl import EdlError
    from ..edl import from_dict as edl_from_dict
    from .captions import map_words

    if not isinstance(edl_data, dict):
        return [None] * len(words)
    try:
        edl = edl_from_dict(edl_data)
    except EdlError:
        return [None] * len(words)
    # one pass, each word named by its index (the first place the reel shows it counts)
    tagged = [{"w": str(i), "t0": w["t0"], "t1": w["t1"]} for i, w in enumerate(words)]
    out: list[tuple | None] = [None] * len(words)
    for m in map_words(tagged, edl.segments, edl.fps):
        i = int(m["w"])
        if out[i] is None:
            out[i] = (m["t0"], m["t1"])
    return out


# --- captions refresh -----------------------------------------------------------------


def patch_captions(job: Path, write: bool = True) -> dict:
    """The human's text in the EDL's caption blocks. Returns {"changed", "rebuilt"}; a
    caption word that needs a word back (an undone hide) or an edited word the captions do
    not carry at its time rebuilds the captions from words.json (`captions --force`)."""
    from .captions import _words as caption_words

    edl_path = job / "work" / "edl.json"
    try:
        data = json.loads(edl_path.read_text())
    except (OSError, ValueError):
        return {"changed": 0, "rebuilt": False}
    caps = data.get("captions") if isinstance(data, dict) else None
    if not caps:
        return {"changed": 0, "rebuilt": False}
    words = caption_words(job)
    times = output_times(words, data)
    by_t0: dict[float, dict] = {}
    for w, tt in zip(words, times, strict=True):
        if tt is not None:
            by_t0.setdefault(round(tt[0], 3), w)

    def source(cw: dict) -> dict | None:
        t = cw.get("t0")
        if not isinstance(t, int | float):
            return None
        for d in (0.0, 0.001, -0.001, CAPTION_S, -CAPTION_S):
            hit = by_t0.get(round(t + d, 3))
            if hit is not None:
                return hit
        return None

    changed, rebuild = 0, False
    for block in caps:
        ws = block.get("words") if isinstance(block, dict) else None
        if not isinstance(ws, list):
            continue
        kept = []
        for cw in ws:
            src = source(cw) if isinstance(cw, dict) else None
            if src is None:
                kept.append(cw)
                continue
            if src.get("hidden"):
                changed += 1
                continue
            if "edit" in src:
                if cw.get("w") != src["w"] or not cw.get("edit"):
                    cw["w"], cw["edit"] = src["w"], True
                    changed += 1
            elif cw.get("edit"):
                cw["w"] = src["w"]  # an edit taken back
                cw.pop("edit")
                changed += 1
            kept.append(cw)
        block["words"] = kept
        if isinstance(block.get("text"), str):
            block["text"] = " ".join(x.get("w", "") for x in kept)
    data["captions"] = [b for b in caps if not isinstance(b, dict) or b.get("words") != []]
    # A word the human ever edited that is in the cut and shown, but not in the captions
    # (a hide taken back, or the captions were made from other times): only a rebuild
    # puts it there.
    ever = {(round(float(e["t0"]), 3), _key(str(e.get("asr", "")))) for e in read_log(job) or []}
    # a patched block longer than the brand's pace allows: regroup it
    from .captions import PACES, job_pace

    try:
        limit = PACES[job_pace(job)].max_chars
    except ToolError:
        limit = None
    for b in data["captions"]:
        ws = b.get("words") if isinstance(b, dict) else None
        if (
            limit
            and isinstance(ws, list)
            and any(isinstance(x, dict) and x.get("edit") for x in ws)
        ):
            if len(" ".join(str(x.get("w", "")) for x in ws if isinstance(x, dict))) > limit:
                rebuild = True
    starts = [
        cw["t0"]
        for b in data["captions"]
        if isinstance(b, dict)
        for cw in b.get("words") or []
        if isinstance(cw, dict) and isinstance(cw.get("t0"), int | float)
    ]
    for w, tt in zip(words, times, strict=True):
        if tt is None or w.get("hidden") or (round(w["t0"], 3), _key(w)) not in ever:
            continue
        if not any(abs(tt[0] - c) <= CAPTION_S for c in starts):
            rebuild = True
            break
    if rebuild:
        return {"changed": changed, "rebuilt": True}
    if changed and write:
        from ..edl import validate

        if errors := validate(data):
            return {"changed": changed, "rebuilt": True, "invalid": errors[:3]}
        write_json(edl_path, data)
    return {"changed": changed, "rebuilt": False}


def refresh(job: Path, say=print) -> int:
    """Captions with the human's text in the finished reel, without Claude: patch the EDL
    captions (or rebuild them), `overlays`, `render`, `verify`, then the overlay record
    (no longer stale). Returns 0 (verify ok) or 4 (verify flagged something), like a run."""
    from .. import run as pipeline

    data = read_job(job)
    brand_id = data.get("brand")
    if not brand_id or not isinstance(data.get("overlays"), dict):
        raise ToolError("the reel has no captions to set again")
    if not pipeline.cut_done(job):
        raise ToolError("the reel is not the finished cut of the approved plan; run the job first")
    from .edl import snap_job

    moved = snap_job(job, write=False).get("moved", 0)
    if moved:
        # render snaps the cut points first: on a reel cut before snapping that would
        # change the cut itself, which only a new cut may do
        raise ToolError(
            f"rendering again would move {moved} cut point(s) (the reel was cut before "
            "cut points were snapped to quiet places); cut it again first"
        )
    cut_sha = pipeline.segments_sha(job)
    say("overlays: setting the captions again from the edited transcript (no Claude run)")
    words_doc = _words_doc(job)
    if n := enforce(job, words_doc["words"]):
        _save(job, words_doc)
        say(f"  restored {n} word edit(s) from the record")
    edl_path = job / "work" / "edl.json"
    edl_before = edl_path.read_bytes()
    try:
        res = patch_captions(job)
        if res["rebuilt"]:
            say("  captions rebuilt from the transcript")
            pipeline.tool(job, "captions", "--force")
            patch_captions(job)  # marks the human's words ("edit") in the new blocks
        else:
            say(f"  {res['changed']} caption word(s) changed")
        pipeline.tool(job, "overlays")
        say("verify: rendering the reel and checking it")
        pipeline.tool(job, "render")
        if pipeline.segments_sha(job) != cut_sha:
            raise ToolError("the render changed the cut (EDL segments); cut it again")
        verify = pipeline.tool(job, "verify", need_ok=False)
    except BaseException:
        # the EDL as the reel was made from it, so the refresh can be tried again
        if edl_path.read_bytes() != edl_before:
            edl_path.write_bytes(edl_before)
        raise
    try:
        verify = json.loads((job / "out" / "verify.json").read_text())
    except (OSError, ValueError):
        pass
    pipeline._record_overlays(job, brand_id, verify)
    ok = bool(verify.get("ok"))
    say(f"verify: {'ok' if ok else 'NOT ok'}")
    return 0 if ok else 4


# --- CLI ------------------------------------------------------------------------------


def show_cmd(args) -> dict:
    job = resolve_job(args.job_ref or args.job)
    words = _words_doc(job)["words"]
    lo = args.t_from if args.t_from is not None else float("-inf")
    hi = args.t_to if args.t_to is not None else float("inf")
    rows = []
    for i, w in enumerate(words):
        if w["t1"] < lo or w["t0"] > hi:
            continue
        mark = " (hidden)" if w.get("hidden") else (" (edited)" if "edit" in w else "")
        orig = f"  [was: {asr(w)}]" if "edit" in w else ""
        print(f"{i}\t{w['t0']:.2f}\t{w['w']}{mark}{orig}")
        rows.append({"i": i, "t0": w["t0"], "t1": w["t1"], "w": w["w"],
                     **{k: w[k] for k in ("edit", "hidden", "w_asr") if k in w}})  # fmt: skip
    return {"job": str(job), "words": len(rows), "edits": sum("edit" in w for w in words),
            "drift": drift(job, words)}  # fmt: skip


def edit_cmd(args) -> dict:
    job = resolve_job(args.job_ref)
    res = edit(job, args.i, args.text)
    w = res["word"]
    shown = "(hidden)" if w.get("hidden") else w["w"]
    print(f"word {res['i']}: {asr(w)!r} -> {shown!r}" if res["changed"] else "unchanged")
    return {"job": str(job), "i": res["i"], "changed": res["changed"],
            "overlays_stale": bool((read_job(job).get("overlays") or {}).get("stale"))}  # fmt: skip


def undo_cmd(args) -> dict:
    job = resolve_job(args.job_ref)
    res = undo(job)
    u = res["undone"]
    print(f"undone: word {res['i']} is {u['from']!r} again (not {u['to']!r})")
    return {"job": str(job), **res}


def register(subparsers) -> None:
    p = subparsers.add_parser("transcript", help="the human's word edits of the transcript")
    sub = p.add_subparsers(dest="transcript_cmd", required=True)
    s = sub.add_parser("show", help="words with index and source time", parents=[job_parent()])
    s.add_argument("job_ref", nargs="?", metavar="job", help="job folder or id")
    s.add_argument("--from", dest="t_from", type=float, help="from this source second")
    s.add_argument("--to", dest="t_to", type=float, help="to this source second")
    s.set_defaults(handler=run_tool("transcript show", show_cmd))
    e = sub.add_parser("edit", help='set the text of word i; "" hides it (human only)')
    e.add_argument("job_ref", metavar="job", help="job folder or id")
    e.add_argument("i", type=int, help="word index (transcript show)")
    e.add_argument("text", help="the new text (empty: hide the word from the captions)")
    e.set_defaults(handler=run_tool("transcript edit", edit_cmd))
    u = sub.add_parser("undo", help="undo the last edit (human only)")
    u.add_argument("job_ref", metavar="job", help="job folder or id")
    u.set_defaults(handler=run_tool("transcript undo", undo_cmd))
