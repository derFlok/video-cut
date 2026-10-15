"""Transcript vocabulary: canonical spellings of names the transcriber gets wrong.

A vocabulary is a list of entries
    {"term": "<canonical spelling>", "variants": ["<known misspelling>", ...], "note"?: str}
kept in brand.json "vocabulary" (the brand's own names, every job of the brand) and in
job.json "vocabulary" (names of this one video, e.g. a customer in a demo). The effective
vocabulary of a job is brand ∪ job; on a conflict (the same term, or a variant claimed by
both) the job's entry wins.

Uses:
- transcription bias (`transcribe.py`): the canonical terms are appended to Whisper's
  initial prompt, within its prompt budget (`prompt_text`);
- deterministic correction (`apply_words`, run by `transcribe` and `vocab apply`): a word
  of work/words.json whose text matches a variant (case-insensitive, punctuation stripped)
  becomes the canonical term. A match may span consecutive words ("Zentro Via"), the
  canonical term split into words is joined again, and an inflected form keeps its ending
  ("Zentrowias" -> "Zentrovias"; also "'s", "es" and hyphen compounds such as
  "Zentrowia-Kunden"). A longer word that merely contains a variant never matches. The
  model's text stays in "w_asr", "vocab" names the term, and words merged from a span keep
  the originals in "parts", so applying again (another vocabulary) starts from the
  transcript as the model wrote it. work/transcribe.json "vocabulary" records the counts;
- suggestions (`suggest`): words within a small edit distance of a term that no variant
  covers are only reported, never applied;
- the skills read the effective vocabulary from `job show` ("vocabulary_effective") and
  use the canonical spelling in every text they write.

    vocab show [job]       the effective vocabulary of a job (brand + job)
    vocab apply [job]      apply it to words.json (no new transcription), to the captions
                           of edl.json / overlays.json when there are any (the overlays are
                           then stale: job.json overlays.stale, re-rendered by the next run)
    vocab suggest [job]    possible misspellings of a term ("did you mean ...?")
"""

import copy
import json
import os
import re
import unicodedata
from collections import Counter
from functools import lru_cache
from pathlib import Path

from .common import ToolError, job_parent, read_job, resolve_job, run_tool, update_job, write_json

MAX_ENTRIES = 200
MAX_VARIANTS = 30
MAX_TEXT = 80
MAX_NOTE = 300
MAX_SPAN = 4  # words one match may span (a name the model split into pieces)
SPLIT_MIN_KEY = 5  # a match over several words needs a key at least this long
# Inflection endings a variant may carry (German genitive/plural, English possessive).
SUFFIXES = ("’s", "'s", "es", "s")
# Variants are literal text: anything that looks like a pattern is refused.
REGEX_CHARS = re.compile(r"[\\^$|*?\[\](){}]")
ENTRY_KEYS = {"term", "variants", "note"}
# Whisper keeps the last n_text_ctx // 2 - 1 = 223 prompt tokens; the filler sample and the
# terms must fit, or the sample would be cut off first.
PROMPT_BUDGET = 223
# Fuzzy suggestions: at most this edit distance, only for words of at least this length.
SUGGEST_MAX_DIST = 2
SUGGEST_MIN_LEN = 4
# mlx_whisper.tokenizer.get_encoding's split pattern
WHISPER_PAT = r"""'s|'t|'re|'ve|'m|'ll|'d| ?\p{L}+| ?\p{N}+| ?[^\s\p{L}\p{N}]+|\s+(?!\S)|\s+"""
SUGGEST_MAX_CONF = 0.9  # a word the model was surer of is left out (`--all` shows it)


class VocabError(Exception):
    """An invalid vocabulary; `errors` lists every problem with its JSON path."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


# --- keys and validation -------------------------------------------------------------


def key(text: str) -> str:
    """Matching key: case-folded letters and digits only (punctuation, spaces and hyphens
    dropped), NFC-normalised so composed and decomposed umlauts compare equal."""
    t = unicodedata.normalize("NFC", text).casefold()
    return "".join(c for c in t if c.isalnum())


def check(value, path: str = "$.vocabulary") -> tuple[list[dict], list[str]]:
    """(clean entries, errors). Clean entries have stripped text, no empty or duplicate
    variants (a variant equal to its own term is dropped)."""
    errors: list[str] = []
    if value is None:
        return [], []
    if not isinstance(value, list):
        return [], [f"{path}: expected a list of {{term, variants}}"]
    if len(value) > MAX_ENTRIES:
        errors.append(f"{path}: at most {MAX_ENTRIES} entries")
    out: list[dict] = []
    owner: dict[str, str] = {}  # key -> where it was first used
    for i, e in enumerate(value[:MAX_ENTRIES]):
        p = f"{path}[{i}]"
        if not isinstance(e, dict):
            errors.append(f"{p}: expected an object {{term, variants}}")
            continue
        extra = sorted(set(e) - ENTRY_KEYS)
        if extra:
            errors.append(f"{p}: unknown field(s) {', '.join(extra)}")
        term = e.get("term")
        if not isinstance(term, str) or not term.strip():
            errors.append(f"{p}.term: required, a non-empty string")
            continue
        term = " ".join(term.split())
        if len(term) > MAX_TEXT:
            errors.append(f"{p}.term: at most {MAX_TEXT} characters")
        tkey = key(term)
        if not tkey:
            errors.append(f"{p}.term: {term!r} has no letters or digits")
            continue
        if tkey in owner:
            errors.append(f"{p}.term: {term!r} is already used by {owner[tkey]}")
            continue
        owner[tkey] = f"{p}.term"
        variants = e.get("variants", [])
        if not isinstance(variants, list):
            errors.append(f"{p}.variants: expected a list of strings")
            variants = []
        if len(variants) > MAX_VARIANTS:
            errors.append(f"{p}.variants: at most {MAX_VARIANTS}")
        clean: list[str] = []
        own: set[str] = set()
        for j, v in enumerate(variants[:MAX_VARIANTS]):
            vp = f"{p}.variants[{j}]"
            if not isinstance(v, str) or not v.strip():
                errors.append(f"{vp}: expected a non-empty string")
                continue
            v = " ".join(v.split())
            if len(v) > MAX_TEXT:
                errors.append(f"{vp}: at most {MAX_TEXT} characters")
                continue
            if REGEX_CHARS.search(v):
                errors.append(f"{vp}: {v!r} must be plain text, not a pattern")
                continue
            vkey = key(v)
            if not vkey:
                errors.append(f"{vp}: {v!r} has no letters or digits")
                continue
            if vkey == tkey:
                # the term in another case or spacing ("Lumocloud"): matched anyway, kept
                # as the human wrote it
                if vkey not in own:
                    own.add(vkey)
                    clean.append(v)
                continue
            if vkey in owner:
                errors.append(f"{vp}: {v!r} is already used by {owner[vkey]}")
                continue
            owner[vkey] = vp
            clean.append(v)
        entry = {"term": term, "variants": clean}
        note = e.get("note")
        if note is not None:
            if not isinstance(note, str) or len(note) > MAX_NOTE:
                errors.append(f"{p}.note: a string of at most {MAX_NOTE} characters")
            elif note.strip():
                entry["note"] = note.strip()
        out.append(entry)
    return out, errors


def validated(value, path: str = "$.vocabulary") -> list[dict]:
    entries, errors = check(value, path)
    if errors:
        raise VocabError(errors)
    return entries


def merge(brand_entries: list[dict], job_entries: list[dict]) -> list[dict]:
    """brand ∪ job; the job wins every conflict. The same term in both is one entry with
    the job's spelling and note and both lists of variants; a brand term the job lists as a
    variant is dropped (the job says that spelling is wrong); a variant both claim for
    different terms belongs to the job's term."""
    job_terms = {key(e["term"]): e for e in job_entries}
    job_variants = {key(v) for e in job_entries for v in e["variants"]}
    claimed = set(job_terms) | job_variants
    out, used = [], set()
    for e in brand_entries:
        tk = key(e["term"])
        if tk in job_variants:
            continue
        own = job_terms.get(tk)
        if own is None:
            out.append({**e, "variants": [v for v in e["variants"] if key(v) not in claimed]})
            continue
        have = {key(v) for v in own["variants"]}
        extra = [v for v in e["variants"] if key(v) not in claimed and key(v) not in have]
        joined = {**copy.deepcopy(e), **copy.deepcopy(own)}
        joined["variants"] = [*own["variants"], *extra]
        out.append(joined)
        used.add(tk)
    return out + [copy.deepcopy(e) for e in job_entries if key(e["term"]) not in used]


def _clean_or_empty(value) -> list[dict]:
    """The valid entries of a stored vocabulary (an invalid one is dropped, not fatal)."""
    entries, errors = check(value)
    return [] if errors else entries


def brand_vocabulary(brand_ref: str | None) -> list[dict]:
    """brand.json "vocabulary" of a brand (empty without one, or when unreadable)."""
    from .. import brand

    if not brand_ref:
        return []
    try:
        d = brand.brand_dir(brand_ref)
        data = json.loads((d / brand.BRAND_FILE).read_text())
    except (brand.BrandError, OSError, ValueError):
        return []
    return _clean_or_empty(data.get("vocabulary")) if isinstance(data, dict) else []


def sources(job: Path, data: dict | None = None) -> dict:
    """{"brand": [...], "job": [...], "effective": [...], "brand_id"}."""
    data = read_job(job) if data is None else data
    b = brand_vocabulary(data.get("brand"))
    j = _clean_or_empty(data.get("vocabulary"))
    return {"brand_id": data.get("brand"), "brand": b, "job": j, "effective": merge(b, j)}


def effective(job: Path, data: dict | None = None) -> list[dict]:
    return sources(job, data)["effective"]


def describe(entries: list[dict]) -> str:
    """One line for humans and skills: `Zentrovia (not: Zentrowia, Sentrovia); Lumo`."""
    parts = []
    for e in entries:
        vs = e["variants"]
        parts.append(f"{e['term']} (not: {', '.join(vs)})" if vs else e["term"])
    return "; ".join(parts)


# --- Whisper prompt -------------------------------------------------------------------


@lru_cache(maxsize=1)
def _encoding():
    """Whisper's multilingual tokenizer (tiktoken + mlx-whisper's vocabulary file, read
    without importing MLX), or None where it is not installed."""
    try:
        import importlib.util

        import tiktoken

        spec = importlib.util.find_spec("mlx_whisper")
        if spec is None or not spec.submodule_search_locations:
            return None
        path = Path(list(spec.submodule_search_locations)[0]) / "assets" / "multilingual.tiktoken"
        import base64

        ranks = {
            base64.b64decode(tok): int(rank)
            for tok, rank in (line.split() for line in path.read_text().splitlines() if line)
        }
        return tiktoken.Encoding(
            name="whisper-multilingual",
            pat_str=WHISPER_PAT,
            mergeable_ranks=ranks,
            special_tokens={},
        )
    except Exception:  # noqa: BLE001 — no tokenizer: fall back to the byte count
        return None


def prompt_tokens(text: str) -> int:
    """Tokens Whisper's prompt takes for `text` (mlx-whisper encodes " " + text). Without
    the tokenizer: the UTF-8 byte count, an upper bound."""
    enc = _encoding()
    s = " " + text.strip()
    return len(enc.encode(s)) if enc is not None else len(s.encode())


def prompt_text(base: str | None, entries: list[dict], budget: int = PROMPT_BUDGET) -> tuple:
    """(prompt, terms used, terms dropped): `base` (the filler sample) followed by the
    canonical terms, deduplicated, as many as fit the budget (job terms come last in the
    effective list, so they are tried first)."""
    terms, seen = [], set()
    for e in reversed(entries):
        k = key(e["term"])
        if k not in seen:
            seen.add(k)
            terms.append(e["term"])
    used: list[str] = []
    for t in terms:
        cand = _join(base, [*used, t])
        if prompt_tokens(cand) <= budget:
            used.append(t)
    dropped = [t for t in terms if t not in used]
    order = [e["term"] for e in entries if e["term"] in used]  # brand first, as listed
    return _join(base, order) if (base or order) else None, order, dropped


def _join(base: str | None, terms: list[str]) -> str:
    tail = (", ".join(terms) + ".") if terms else ""
    return " ".join(x for x in (base or "", tail) if x)


# --- matching -------------------------------------------------------------------------

_EDGE = re.compile(r"^([^\w]*)(.*?)([^\w]*)$", re.S)


def _split(text: str) -> tuple[str, str, str]:
    """(leading punctuation, core, trailing punctuation)."""
    m = _EDGE.match(text)
    return (m.group(1), m.group(2), m.group(3)) if m else ("", text, "")


class Matcher:
    """Finds vocabulary matches in a list of word texts."""

    def __init__(self, entries: list[dict]):
        self.index: dict[str, dict] = {}
        for e in entries:
            self.index.setdefault(key(e["term"]), e)
            for v in e["variants"]:
                self.index.setdefault(key(v), e)

    def __bool__(self) -> bool:
        return bool(self.index)

    def _hit(self, cores: list[str]) -> tuple[dict, str] | None:
        """(entry, ending as written) when these word cores spell a term or variant."""
        full = "".join(key(c) for c in cores)
        multi = len(cores) > 1
        if full in self.index and (not multi or len(full) >= SPLIT_MIN_KEY):
            return self.index[full], ""
        last = cores[-1]
        for suf in SUFFIXES:
            if len(last) > len(suf) and last.casefold().endswith(suf):
                base = "".join(key(c) for c in cores[:-1]) + key(last[: -len(suf)])
                if base in self.index and (not multi or len(base) >= SPLIT_MIN_KEY):
                    return self.index[base], last[-len(suf) :]
        return None

    def _single(self, text: str) -> tuple[str, dict] | None:
        """(new text, entry) for one word, also for hyphen compounds part by part."""
        lead, core, trail = _split(text)
        if not core:
            return None
        hit = self._hit([core])
        if hit:
            e, suf = hit
            return lead + e["term"] + suf + trail, e
        if "-" in core:
            parts, changed, entry = core.split("-"), False, None
            for i, part in enumerate(parts):
                h = self._hit([part]) if part else None
                if h:
                    parts[i] = h[0]["term"] + h[1]
                    changed, entry = True, h[0]
            if changed:
                return lead + "-".join(parts) + trail, entry
        return None

    def find(self, texts: list[str]) -> list[tuple[int, int, str, dict]]:
        """Non-overlapping matches (start, end exclusive, new text, entry), longest span
        first, left to right. A span never crosses punctuation inside it."""
        out, i, n = [], 0, len(texts)
        while i < n:
            found = None
            for span in range(min(MAX_SPAN, n - i), 1, -1):
                parts = [_split(t) for t in texts[i : i + span]]
                if any(p[2] for p in parts[:-1]) or any(p[0] for p in parts[1:]):
                    continue  # "Zentro. Via" is two sentences, not one name
                cores = [p[1] for p in parts]
                if not all(cores):
                    continue
                hit = self._hit(cores)
                if hit:
                    e, suf = hit
                    found = (i, i + span, parts[0][0] + e["term"] + suf + parts[-1][2], e)
                    break
            if found is None:
                one = self._single(texts[i])
                if one:
                    found = (i, i + 1, one[0], one[1])
            if found:
                out.append(found)
                i = found[1]
            else:
                i += 1
        return out


def _retime(parts: list[dict], w: dict) -> list[dict]:
    """The parts of a merged word fitted into its current times (alignment, --no-align or
    the video time shift may have moved the merged word since it was made)."""
    out = [dict(p) for p in parts]
    for a, b in (("t0", "t1"), ("t0_asr", "t1_asr")):
        if a not in w or b not in w or any(a not in p or b not in p for p in out):
            for p in out:
                if a.endswith("_asr"):
                    p.pop(a, None)
                    p.pop(b, None)
            continue
        lo, hi = out[0][a], out[-1][b]
        scale = (w[b] - w[a]) / (hi - lo) if hi > lo else 0.0
        for p in out:
            p[a] = round(w[a] + (p[a] - lo) * scale, 3)
            p[b] = round(w[a] + (p[b] - lo) * scale, 3)
    return out


def restore(words: list[dict]) -> list[dict]:
    """The transcript as the model wrote it: every correction undone."""
    out = []
    for w in words:
        if "parts" in w:
            out += _retime(w["parts"], w)
            continue
        w = dict(w)
        if "w_asr" in w:
            w["w"] = w.pop("w_asr")
        w.pop("vocab", None)
        out.append(w)
    return out


def _merge_words(group: list[dict], text: str, term: str) -> dict:
    first, last = group[0], group[-1]
    w = {**first, "w": text, "t1": last["t1"]}
    if "t1_asr" in last:
        w["t1_asr"] = last["t1_asr"]
    confs = [g.get("conf") for g in group if g.get("conf") is not None]
    w["conf"] = min(confs) if confs else None
    w["w_asr"] = " ".join(g["w"] for g in group)
    w["vocab"] = term
    w["parts"] = [dict(g) for g in group]
    return w


def apply_words(words: list[dict], entries: list[dict]) -> list[dict]:
    """words.json words with the vocabulary applied, always from the model's own text."""
    words = restore(words)
    m = Matcher(entries)
    if not m:
        return words
    hits = {s: (e, text, entry) for s, e, text, entry in m.find([w["w"] for w in words])}
    out, i = [], 0
    while i < len(words):
        if i not in hits:
            out.append(words[i])
            i += 1
            continue
        end, text, entry = hits[i]
        group = words[i:end]
        if len(group) == 1:
            if text != group[0]["w"]:
                out.append({**group[0], "w": text, "w_asr": group[0]["w"], "vocab": entry["term"]})
            else:
                out.append(group[0])
        else:
            out.append(_merge_words(group, text, entry["term"]))
        i = end
    return out


def counts(words: list[dict]) -> dict:
    """{"applied": corrected words, "terms": {term: n}}."""
    c = Counter(w["vocab"] for w in words if w.get("vocab"))
    return {"applied": sum(c.values()), "terms": dict(sorted(c.items()))}


def apply_captions(captions: list, entries: list[dict]) -> int:
    """Correct caption words in place (one caption block at a time); returns how many
    caption words changed. Captions are corrected forwards: their text may be the agent's."""
    m = Matcher(entries)
    if not m or not isinstance(captions, list):
        return 0
    changed = 0
    for block in captions:
        ws = block.get("words") if isinstance(block, dict) else None
        if not isinstance(ws, list) or not all(
            isinstance(w, dict) and isinstance(w.get("w"), str) for w in ws
        ):
            continue
        out, i = [], 0
        hits = {s: (e, t) for s, e, t, _ in m.find([w["w"] for w in ws])}
        while i < len(ws):
            if i not in hits:
                out.append(ws[i])
                i += 1
                continue
            end, text = hits[i]
            group = ws[i:end]
            if len(group) == 1 and text == group[0]["w"]:
                out.append(group[0])
            else:
                w = {**group[0], "w": text, "t1": group[-1].get("t1", group[0].get("t1"))}
                w.setdefault("w_asr", " ".join(g["w"] for g in group))
                if any(g.get("emph") for g in group):
                    w["emph"] = True
                out.append(w)
                changed += len(group)
            i = end
        block["words"] = out
        if isinstance(block.get("text"), str):
            block["text"] = " ".join(w["w"] for w in out)
    return changed


# --- suggestions ----------------------------------------------------------------------


def distance(a: str, b: str, limit: int = SUGGEST_MAX_DIST) -> int:
    """Levenshtein distance, or limit + 1 once it is certainly larger."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def suggestions(words: list[dict], entries: list[dict], all_conf: bool = False) -> list[dict]:
    """Possible misspellings: words (and pairs of words) within a small edit distance of a
    term or variant that no variant covers yet, grouped by what was heard."""
    if not entries:
        return []
    words = restore(words)
    m = Matcher(entries)
    covered = set()
    for s, e, _, _ in m.find([w["w"] for w in words]):
        covered.update(range(s, e))
    targets = []
    for e in entries:
        for t in (e["term"], *e["variants"]):
            k = key(t)
            if len(k) >= SUGGEST_MIN_LEN:
                targets.append((k, e))
    found: dict[tuple[str, str], dict] = {}
    for i in range(len(words)):
        for span in (1, 2):
            group = words[i : i + span]
            if len(group) < span or any(j in covered for j in range(i, i + span)):
                continue
            heard = " ".join(_split(g["w"])[1] for g in group)
            hk = key(heard)
            if len(hk) < SUGGEST_MIN_LEN:
                continue
            confs = [g.get("conf") for g in group if g.get("conf") is not None]
            conf = min(confs) if confs else None
            if not all_conf and conf is not None and conf >= SUGGEST_MAX_CONF:
                continue
            best = None
            for k, e in targets:
                limit = 1 if len(k) < 7 else SUGGEST_MAX_DIST
                d = distance(hk, k, limit)
                if 0 < d <= limit and (best is None or d < best[0]):
                    best = (d, e)
            if best is None:
                continue
            d, e = best
            item = found.setdefault(
                (e["term"], hk),
                {"term": e["term"], "heard": heard, "distance": d, "count": 0, "times": [],
                 "conf": conf},
            )  # fmt: skip
            item["count"] += 1
            if len(item["times"]) < 10:
                item["times"].append(group[0]["t0"])
            if conf is not None and (item["conf"] is None or conf < item["conf"]):
                item["conf"] = conf
    return sorted(found.values(), key=lambda x: (x["distance"], -x["count"], x["heard"]))


# --- job operations -------------------------------------------------------------------


def _read(p: Path):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def apply_job(
    job: Path, entries: list[dict] | None = None, captions: bool = True, candidates: bool = True
) -> dict:
    """Apply the effective vocabulary to an existing transcript (no new transcription):
    words.json, then the packed transcript and candidates, then the captions of edl.json
    and overlays.json when there are any. Returns the counts."""
    work = job / "work"
    words_file = work / "words.json"
    doc = _read(words_file)
    if not isinstance(doc, dict) or not isinstance(doc.get("words"), list):
        raise ToolError("work/words.json is missing; run `videocut tool transcribe` first")
    entries = effective(job) if entries is None else entries
    before = [w["w"] for w in doc["words"]]
    doc["words"] = apply_words(doc["words"], entries)
    words_changed = before != [w["w"] for w in doc["words"]]
    if words_changed:
        write_json(words_file, doc)
    record = {**counts(doc["words"]), "entries": len(entries)}
    meta_file = work / "transcribe.json"
    meta = _read(meta_file)
    if isinstance(meta, dict) and meta.get("vocabulary") != record:
        meta["vocabulary"] = record
        write_json(meta_file, meta)
    if words_changed and candidates and (work / "audio.wav").is_file():
        from .transcribe import make_candidates

        make_candidates(work)  # takes_packed.md shows the corrected words
    caption_words = 0
    if captions:
        for name in ("edl.json", "overlays.json"):
            data = _read(work / name)
            if isinstance(data, dict) and data.get("captions"):
                n = apply_captions(data["captions"], entries)
                if n:
                    write_json(work / name, data)
                    caption_words = max(caption_words, n)
    stale = False
    if caption_words:
        rec = read_job(job).get("overlays")
        stale = isinstance(rec, dict)  # an overlay record the next run must redo
        if stale:
            update_job(job, overlays={**rec, "stale": "vocabulary"})
    return {
        **record,
        "words_changed": words_changed,
        "caption_words": caption_words,
        "overlays_stale": stale,
    }


# --- CLI ------------------------------------------------------------------------------


def _human_only(what: str) -> None:
    from .job import AGENT_ENV

    if os.environ.get(AGENT_ENV):
        raise ToolError(f"only a human edits the {what} (not available inside an agent run)")


def edit(entries: list[dict], op: str, term: str, variants: list[str], note: str | None):
    """`add` (a new term, or more variants for a known one) or `remove` (variants, or the
    whole term when none are given) on a list of entries; returns the validated list."""
    entries = copy.deepcopy(entries)
    tk = key(term)
    if not tk:
        raise ToolError(f"{term!r} has no letters or digits")
    idx = next((i for i, e in enumerate(entries) if key(e["term"]) == tk), None)
    if op == "add":
        if idx is None:
            entries.append({"term": term, "variants": list(variants)})
            idx = len(entries) - 1
        else:
            have = {key(v) for v in entries[idx]["variants"]}
            entries[idx]["variants"] += [v for v in variants if key(v) not in have]
        if note is not None:
            entries[idx]["note"] = note
    else:
        if idx is None:
            raise ToolError(f"{term!r} is not in the vocabulary")
        if variants:
            drop = {key(v) for v in variants}
            entries[idx]["variants"] = [v for v in entries[idx]["variants"] if key(v) not in drop]
        else:
            entries.pop(idx)
    try:
        return validated(entries)
    except VocabError as e:
        raise ToolError(str(e)) from None


def parse_flag(values: list[str] | None) -> list[dict]:
    """`--vocab "Term=variant1,variant2"` (repeatable; a bare "Term" has no variants)."""
    entries = []
    for raw in values or []:
        term, _, rest = raw.partition("=")
        variants = [v.strip() for v in rest.split(",") if v.strip()]
        entries.append({"term": term.strip(), "variants": variants})
    try:
        return validated(entries, "--vocab")
    except VocabError as e:
        raise ToolError(str(e)) from None


def _print(entries: list[dict]) -> None:
    for e in entries:
        note = f"\t({e['note']})" if "note" in e else ""
        print(f"{e['term']}\t{', '.join(e['variants'])}{note}")


def job_vocab_cmd(args) -> dict:
    job = resolve_job(args.job)
    data = read_job(job)
    entries = _clean_or_empty(data.get("vocabulary"))
    if args.vocab_op != "list":
        _human_only("vocabulary of a job")
        entries = edit(entries, args.vocab_op, args.term, args.variants, args.note)
        update_job(job, vocabulary=entries)
    _print(entries)
    src = sources(job)
    return {"job": str(job), "vocabulary": entries, "effective": src["effective"]}


def brand_vocab_cmd(args) -> dict:
    from .. import brand

    try:
        d = brand.brand_dir(args.brand)
    except brand.BrandError as e:
        raise ToolError(str(e)) from None
    raw = _read(d / brand.BRAND_FILE)
    if not isinstance(raw, dict):
        raise ToolError(f"{args.brand}: brand.json cannot be read")
    entries, errors = check(raw.get("vocabulary"))
    if args.vocab_op != "list":
        _human_only("vocabulary of a brand")
        if d.resolve() == (brand.BUNDLED_ROOT / brand.EXAMPLE_ID).resolve():
            raise ToolError("the bundled example brand is read-only; create a new brand")
        if errors:
            raise ToolError("; ".join(errors))
        entries = edit(entries, args.vocab_op, args.term, args.variants, args.note)
        raw["vocabulary"] = entries
        write_json(d / brand.BRAND_FILE, raw)
    elif errors:
        raise ToolError("; ".join(errors))
    _print(entries)
    return {"brand": args.brand, "vocabulary": entries}


def show_cmd(args) -> dict:
    job = resolve_job(args.job_ref or args.job)
    src = sources(job)
    _print(src["effective"])
    meta = _read(job / "work" / "transcribe.json")
    return {
        "job": str(job),
        **src,
        "applied": meta.get("vocabulary") if isinstance(meta, dict) else None,
    }


def apply_cmd(args) -> dict:
    job = resolve_job(args.job_ref or args.job)
    res = apply_job(job)
    line = f"{job.name}: {res['applied']} word(s) in the canonical spelling"
    if res["caption_words"]:
        line += f", {res['caption_words']} caption word(s) corrected"
    if res["overlays_stale"]:
        line += "; the overlays are stale (re-rendered by the next run)"
    print(line)
    return {"job": str(job), **res}


def suggest_cmd(args) -> dict:
    job = resolve_job(args.job_ref or args.job)
    doc = _read(job / "work" / "words.json")
    if not isinstance(doc, dict) or not isinstance(doc.get("words"), list):
        raise ToolError("work/words.json is missing; run `videocut tool transcribe` first")
    items = suggestions(doc["words"], effective(job), all_conf=args.all)
    for s in items:
        print(f"{s['heard']!r} -> {s['term']}  ({s['count']}x, distance {s['distance']})")
    return {"job": str(job), "suggestions": items}


def _edit_parser(sub, name: str, owner: str, owner_help: str, handler) -> None:
    p = sub.add_parser(name, help=f"the transcript vocabulary of a {owner}")
    p.add_argument(owner, help=owner_help)
    ops = p.add_subparsers(dest="vocab_op", required=True)
    for op, text in (("add", "add a term or more variants of it (human only)"),
                     ("remove", "remove variants, or the term without variants (human only)"),
                     ("list", "list the entries")):  # fmt: skip
        o = ops.add_parser(op, help=text)
        if op != "list":
            o.add_argument("term", help="the canonical spelling")
            o.add_argument("variants", nargs="*", help="misspellings the transcriber writes")
        if op == "add":
            o.add_argument("--note", help="a short note (who or what it is)")
        o.set_defaults(handler=run_tool(f"{owner} vocab {op}", handler), term=None,
                       variants=[], note=None)  # fmt: skip


def register_job(sub) -> None:
    """`videocut tool job vocab <job> add|remove|list ...` (called by job.py)."""
    _edit_parser(sub, "vocab", "job", "job folder or id", job_vocab_cmd)


def register_brand(sub) -> None:
    """`videocut tool brand vocab <brand> add|remove|list ...` (called by tools/brand.py)."""
    _edit_parser(sub, "vocab", "brand", "brand id (or a brand folder)", brand_vocab_cmd)


def register(subparsers) -> None:
    p = subparsers.add_parser("vocab", help="transcript vocabulary: show, apply, suggest")
    sub = p.add_subparsers(dest="vocab_cmd", required=True)
    for name, fn, text in (
        ("show", show_cmd, "the effective vocabulary of a job (brand + job)"),
        ("apply", apply_cmd, "apply it to the existing transcript and captions"),
        ("suggest", suggest_cmd, "possible misspellings of a term (never applied)"),
    ):
        c = sub.add_parser(name, help=text, parents=[job_parent()])
        c.add_argument("job_ref", nargs="?", metavar="job", help="job folder or id")
        if name == "suggest":
            c.add_argument("--all", action="store_true", help="also confident words")
        c.set_defaults(handler=run_tool(f"vocab {name}", fn))
