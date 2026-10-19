"""`videocut tool material fetch|generate|choose|show [job]`: B-roll and stills for the
material wishes of the approved plan (job.json "material", tools/_job_material.py).

The plan run (agent side, no network) writes `material_wishes` into work/plan.json:
[{n, kind: broll|still|icon, src_from, src_to, query, prompt, style: photo|illustration,
why}]. After the human approved the plan, on the host:

    fetch     searches the stock libraries (settings "stock_providers": Pexels videos and
              photos, Coverr videos) with each broll/still wish's query, the job's
              orientation and locale, and downloads up to MAX_STOCK candidates per wish
              (the smallest rendition that covers the output size, clips cut to
              MAX_CLIP_S); with mode generated/both the image generator (settings
              "image_provider") makes up to MAX_GEN candidates per still/icon wish from its
              prompt, the brand palette and the style. `--wish N` redoes one wish, with
              `--query` / `--prompt` the human's edited text
    generate  only the generator part (`--wish N [--prompt]`)
    choose    `choose <job> <n> <candidate id|none>`: the human's pick (human only, like
              `job approve`); the cut may use only chosen material
    show      the table: wishes, candidates, choices

Everything is logged in work/material.json (version 1):
{"version": 1, "wishes": [{n, kind, src_from, src_to, query, prompt, style, why,
  "chosen": id|null, "decided": bool, "candidates": [{id, kind: video|image, file, thumb,
  width, height, duration_s, source, source_url, author, licence, attribution,
  ai_generated, model, prompt, seed, watermark, sha256, source_sha256, fetched}]}],
 "cost_usd": spent on generation, "runs": [{at, wishes, stock, generated, cost_usd,
 wall_s, errors}]}
Files live in work/material/<n>/ (cN.mp4|jpg|png and cN_sm.jpg, ≤ THUMB px). A generated
image is stored exactly as the generator returned it (its SynthID or C2PA stays intact);
`source_sha256` is the hash of that original.

What was fetched is kept outside the job folder too ($VIDEOCUT_HOME/state/<job>/material.json,
the trusted copy; work/material.json mirrors it for the skills and the app). The human's
choices are trusted only from a record outside the job folder (agent runs may
write work/): $VIDEOCUT_HOME/state/<job>/material_choice.json {"choices": {"<n>":
{"candidate", "sha256", "file", "kind", "ai_generated", "wish"}}}; material.json "chosen"
mirrors it for display. `chosen()` returns a choice only while the wish is still the one
of the approved plan and the file still has the recorded SHA-256.

Network only here, on the host: fetch/generate refuse inside an agent run
(VIDEOCUT_AGENT=1), and the cost guard refuses generation above settings
"material_budget_usd" per job (estimated from the providers' published prices).
"""

import hashlib
import json
import os
import random
import shutil
import time
from datetime import datetime
from pathlib import Path

from .. import settings
from ..paths import state_dir
from . import _format, _job_material
from ._material_providers import ProviderError, StockHit, get_provider
from .common import (
    ToolError,
    ffmpeg_to,
    job_parent,
    read_job,
    resolve_job,
    run_tool,
    write_json,
)

FILE = "work/material.json"
DIR = "work/material"
VERSION = 1
KINDS = ("broll", "still", "icon")
STYLES = ("photo", "illustration")
MAX_STOCK = 4
MAX_GEN = 2
MAX_CLIP_S = 30.0
THUMB = 480
WISH_EVERY_S = 8.0  # at most one wish per this many seconds of target length
QUERY_WORDS = (2, 8)  # a stock query has this many words (the skill asks for 3-6)
PROMPT_MAX = 600
LOCALES = {"de": "de-DE", "en": "en-US"}
CHOICE_FILE = "material_choice.json"
INDEX_FILE = "material.json"  # the trusted copy of work/material.json, in state/<job>/


class MaterialError(ToolError):
    pass


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------ wishes of the plan


def _num(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def max_wishes(length_s: float | None) -> int:
    return int((length_s or 0) // WISH_EVERY_S)


def plan_length(plan: dict) -> float | None:
    """The length the wishes are counted against: the plan's target, else its estimate,
    else the sum of its keep rows."""
    for key in ("target_s", "estimated_s"):
        if _num(plan.get(key)) and plan[key] > 0:
            return float(plan[key])
    total = 0.0
    for it in plan.get("keep_list") or []:
        src = it.get("src") if isinstance(it, dict) else None
        if isinstance(src, list) and len(src) == 2 and all(map(_num, src)):
            total += max(0.0, src[1] - src[0])
    return total or None


def wish_errors(plan: dict, mode: str, duration: float | None = None) -> list[str]:
    """Problems with plan.json "material_wishes" (tools/plan.py calls this)."""
    items = plan.get("material_wishes")
    if items is None:
        return []
    if not isinstance(items, list):
        return ["$.material_wishes: must be a list"]
    if mode == "none" and items:
        return ['$.material_wishes: the job has no material (job.json "material" mode none); '
                "leave it empty"]  # fmt: skip
    errs: list[str] = []
    limit = max_wishes(plan_length(plan))
    if len(items) > limit:
        errs.append(
            f"$.material_wishes: {len(items)} wishes; at most one per {WISH_EVERY_S:g} s of "
            f"the target length ({limit} here)"
        )
    keep = [it.get("src") for it in plan.get("keep_list") or [] if isinstance(it, dict)]
    keep = [k for k in keep if isinstance(k, list) and len(k) == 2 and all(map(_num, k))]
    ns = []
    for i, w in enumerate(items):
        p = f"$.material_wishes[{i}]"
        if not isinstance(w, dict):
            errs.append(f"{p}: must be an object")
            continue
        n = w.get("n")
        if type(n) is not int or n < 1:
            errs.append(f"{p}.n: must be an integer >= 1")
        if type(n) is int:
            ns.append(n)
        kind = w.get("kind")
        if kind not in KINDS:
            errs.append(f"{p}.kind: must be one of {', '.join(KINDS)}")
        elif mode == "stock" and kind == "icon":
            errs.append(f"{p}.kind: icon needs a generator; the mode is stock")
        elif mode == "generated" and kind == "broll":
            errs.append(f"{p}.kind: broll is stock video; the mode is generated")
        a, b = w.get("src_from"), w.get("src_to")
        if not (_num(a) and _num(b) and 0 <= a < b):
            errs.append(f"{p}.src_from/src_to: need source seconds 0 <= src_from < src_to")
        else:
            if duration is not None and b > duration + 0.5:
                errs.append(f"{p}.src_to: {b} is beyond the source duration {duration:.2f}")
            if keep and not any(k[0] - 0.05 <= a and b <= k[1] + 0.05 for k in keep):
                errs.append(f"{p}: [{a}, {b}] must lie inside one keep-list row")
            if b - a < 1.0:
                errs.append(f"{p}: the span is shorter than 1 s")
        q = w.get("query")
        if kind in ("broll", "still") and mode in ("stock", "both"):
            words = str(q or "").split()
            if not QUERY_WORDS[0] <= len(words) <= QUERY_WORDS[1]:
                errs.append(f"{p}.query: 3-6 English stock keywords")
        if q is not None and not isinstance(q, str):
            errs.append(f"{p}.query: must be a string")
        pr = w.get("prompt")
        if kind in ("still", "icon") and mode in ("generated", "both"):
            if not (isinstance(pr, str) and pr.strip()):
                errs.append(f"{p}.prompt: one English sentence for the generator")
        if pr is not None and not (isinstance(pr, str) and len(pr) <= PROMPT_MAX):
            errs.append(f"{p}.prompt: a string of at most {PROMPT_MAX} characters")
        if w.get("style") not in STYLES:
            errs.append(f"{p}.style: must be one of {', '.join(STYLES)}")
        if not (isinstance(w.get("why"), str) and w["why"].strip()):
            errs.append(f"{p}.why: must be a string")
    if len(ns) != len(set(ns)):
        errs.append("$.material_wishes: numbers n must be unique")
    return errs


def _signature(w: dict) -> list:
    """What identifies a wish of the approved plan (an edited query or prompt keeps it)."""
    return [w.get("n"), w.get("kind"), w.get("src_from"), w.get("src_to")]


def approved_wishes(job: Path) -> list[dict]:
    """The material wishes of the approved plan (work/plan.json the human saw); [] when the
    job has no material, no plan.json was approved or it lists none."""
    from .job import approved_plan_json, is_approved

    if _job_material.stored(read_job(job))["mode"] == "none" or not is_approved(job):
        return []
    if not approved_plan_json(job):
        return []
    try:
        plan = json.loads((job / "work" / "plan.json").read_text())
    except (OSError, ValueError):
        return []
    return [w for w in plan.get("material_wishes") or [] if isinstance(w, dict)]


# ------------------------------------------------------------------ material.json


def index_path(job: Path) -> Path:
    return state_dir() / job.resolve().name / INDEX_FILE


def load(job: Path) -> dict:
    """What the host fetched (the trusted copy outside the job folder; work/material.json
    only mirrors it, so an agent run that edits that file changes nothing)."""
    try:
        data = json.loads(index_path(job).read_text())
    except (OSError, ValueError):
        return {"version": VERSION, "wishes": [], "cost_usd": 0.0, "runs": []}
    if not isinstance(data, dict) or not isinstance(data.get("wishes"), list):
        return {"version": VERSION, "wishes": [], "cost_usd": 0.0, "runs": []}
    return data


def save(job: Path, data: dict) -> None:
    """Store the trusted copy and its mirror work/material.json."""
    index_path(job).parent.mkdir(parents=True, exist_ok=True)
    write_json(index_path(job), data)
    (job / "work").mkdir(exist_ok=True)
    write_json(job / FILE, data)


def synced(job: Path, write: bool = True) -> dict:
    """material.json with exactly the approved plan's wishes (in plan order): a wish whose
    signature is unchanged keeps its candidates and the human's edited query/prompt.
    `write`: store it when it changed (readers such as the app's GET pass False, so they
    never race a running fetch)."""
    data = load(job)
    have = {w.get("n"): w for w in data["wishes"] if isinstance(w, dict)}
    out = []
    for w in approved_wishes(job):
        old = have.get(w.get("n"))
        keep = old is not None and _signature(old) == _signature(w)
        item = {k: w.get(k) for k in ("n", "kind", "src_from", "src_to", "query", "prompt",
                                      "style", "why")}  # fmt: skip
        if keep:
            for k in ("query", "prompt"):
                if old.get(f"{k}_edited"):
                    item[k], item[f"{k}_edited"] = old.get(k), True
        item["candidates"] = old.get("candidates", []) if keep else []
        out.append(item)
    data["wishes"] = out
    data["version"] = VERSION
    _mirror(job, data, write)
    return data


# ------------------------------------------------------------------ the human's choices


def choice_path(job: Path) -> Path:
    return state_dir() / job.resolve().name / CHOICE_FILE


def _choices(job: Path) -> dict:
    try:
        data = json.loads(choice_path(job).read_text())
        return data.get("choices") if isinstance(data.get("choices"), dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def decisions(job: Path) -> dict[int, dict | None]:
    """{wish n: the chosen candidate (from material.json, file checked) or None for
    "none"} for every wish of the approved plan the human decided. A choice whose wish
    changed or whose file was replaced does not count."""
    rec = _choices(job)
    data = load(job)
    cands = {w.get("n"): w for w in data["wishes"] if isinstance(w, dict)}
    out: dict[int, dict | None] = {}
    for w in approved_wishes(job):
        c = rec.get(str(w.get("n")))
        if not isinstance(c, dict) or c.get("wish") != _signature(w):
            continue
        if c.get("candidate") is None:
            out[w["n"]] = None
            continue
        cand = next((x for x in (cands.get(w["n"]) or {}).get("candidates", [])
                     if x.get("id") == c["candidate"]), None)  # fmt: skip
        f = (job / str(c.get("file") or "")).resolve()
        if (
            cand is None
            or cand.get("file") != c.get("file")
            or cand.get("sha256") != c.get("sha256")
        ):
            continue
        if not f.is_relative_to((job / DIR).resolve()) or not f.is_file() or _sha(f) != c["sha256"]:
            continue
        out[w["n"]] = {**cand, "n": w["n"], "wish_kind": w.get("kind"), "style": w.get("style"),
                       "src_from": w.get("src_from"), "src_to": w.get("src_to")}  # fmt: skip
    return out


def chosen(job: Path) -> dict[int, dict]:
    """{wish n: chosen candidate} (decisions without the "none" ones)."""
    return {n: c for n, c in decisions(job).items() if c is not None}


def pending(job: Path) -> list[int]:
    """Wishes of the approved plan the human has not decided yet."""
    done = decisions(job)
    return [w["n"] for w in approved_wishes(job) if w.get("n") not in done]


def choices_sha(job: Path) -> str | None:
    """Hash of the decisions (a changed choice makes the cut stale); None without wishes."""
    if not approved_wishes(job):
        return None
    d = {str(n): (c or {}).get("sha256") for n, c in sorted(decisions(job).items())}
    return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()


def _mirror(job: Path, data: dict, write: bool = True) -> None:
    """Copy the trusted decisions into `data` (display only) and store it when it changed."""
    dec = decisions_from(job, data)
    for w in data["wishes"]:
        n = w.get("n")
        w["decided"] = n in dec
        w["chosen"] = dec.get(n)
    if write and data != load(job):
        save(job, data)


def decisions_from(job: Path, data: dict) -> dict:
    """{n: candidate id or None} of the trusted record for the wishes in `data`."""
    rec = _choices(job)
    out = {}
    for w in data["wishes"]:
        c = rec.get(str(w.get("n")))
        if isinstance(c, dict) and c.get("wish") == _signature(w):
            ids = {x.get("id") for x in w.get("candidates", [])}
            if c.get("candidate") is None or c["candidate"] in ids:
                out[w["n"]] = c.get("candidate")
    return out


def choose(job: Path, n: int, candidate: str | None, say=print) -> dict:
    """Record the human's pick for wish n (a candidate id, or None for none)."""
    _job_material.human_only("chooses material")
    data = synced(job)
    w = next((x for x in data["wishes"] if x.get("n") == n), None)
    if w is None:
        raise MaterialError(f"no material wish {n} in the approved plan")
    entry = {"candidate": None, "wish": _signature(w), "at": now()}
    if candidate is not None:
        cand = next((c for c in w.get("candidates", []) if c.get("id") == candidate), None)
        if cand is None:
            ids = ", ".join(c["id"] for c in w.get("candidates", [])) or "none fetched"
            raise MaterialError(f"wish {n} has no candidate {candidate!r} ({ids})")
        f = (job / cand["file"]).resolve()
        if not f.is_relative_to((job / DIR).resolve()) or not f.is_file():
            raise MaterialError(f"{cand['file']} is missing; fetch wish {n} again")
        if _sha(f) != cand.get("sha256"):
            raise MaterialError(
                f"{cand['file']} changed since it was fetched; fetch wish {n} again"
            )
        entry |= {
            "candidate": candidate,
            "file": cand["file"],
            "sha256": cand["sha256"],
            "kind": cand.get("kind"),
            "ai_generated": bool(cand.get("ai_generated")),
        }
        if cand.get("source") == "coverr":
            _report_coverr(cand)
    path = choice_path(job)
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = _choices(job)
    rec[str(n)] = entry
    write_json(path, {"job": str(job.resolve()), "choices": rec})
    _mirror(job, data)
    say(f"wish {n}: " + (f"chose {candidate}" if candidate else "no material"))
    return {"n": n, "chosen": candidate, "pending": pending(job)}


def choose_all_none(job: Path, say=print) -> list[int]:
    """`videocut run --material-skip`: no material for every undecided wish."""
    left = pending(job)
    for n in left:
        choose(job, n, None, say)
    return left


def _report_coverr(cand: dict) -> None:
    try:
        prov = get_provider("coverr")
        prov.report_download(
            StockHit(
                id=cand.get("stock_id") or "",
                kind="video",
                width=0,
                height=0,
                duration_s=None,
                preview_url="",
                file_url="",
                author="",
                source_url="",
                licence="",
                attribution="",
            )
        )
    except ProviderError:
        pass  # counting the download is best effort; the choice stands


# ------------------------------------------------------------------ fetching


def orientation(job: Path) -> str:
    w, h = _format.FORMATS[_format.job_format(read_job(job))]
    return "portrait" if h > w * 1.05 else "landscape" if w > h * 1.05 else "square"


def output_size(job: Path) -> tuple[int, int]:
    return _format.FORMATS[_format.job_format(read_job(job))]


def palette(job: Path) -> list[str]:
    """The brand's colour hex values (bg, surface, accent, text), or none without a brand."""
    ref = read_job(job).get("brand")
    if not ref:
        return []
    from .. import brand

    try:
        tokens = brand.load(ref)
    except Exception:  # noqa: BLE001 — a broken brand only loses the palette
        return []
    colors = tokens.get("colors") or {}
    out = [colors.get(k) for k in ("accent", "bg", "surface", "text")]
    return [c for c in out if isinstance(c, str) and c.startswith("#")]


def _probe(path: Path) -> dict:
    from .ingest import probe

    info = probe(path, need_audio=False)
    w, h = info["size"]
    return {"width": w, "height": h, "duration": info.get("duration") or 0.0, "info": info}


def _thumb(src: Path, dst: Path, video: bool) -> None:
    seek = ["-ss", "1"] if video else []
    fit = f"scale='min({THUMB},iw)':'min({THUMB},ih)':force_original_aspect_ratio=decrease"
    ffmpeg_to(dst, *seek, "-i", str(src), "-frames:v", "1", "-vf", f"{fit},format=yuvj420p",
              "-q:v", "4")  # fmt: skip


def _trim(src: Path, seconds: float) -> None:
    """Cut a downloaded clip to `seconds` in place (no re-encode, no audio)."""
    tmp = src.with_name(src.stem + ".trim" + src.suffix)
    ffmpeg_to(tmp, "-i", str(src), "-t", f"{seconds:.3f}", "-an", "-c", "copy")
    tmp.replace(src)


def _clean(folder: Path, keep: set[str]) -> None:
    if not folder.is_dir():
        return
    for p in folder.iterdir():
        if p.is_file() and p.stem.split("_")[0] not in keep:
            p.unlink()


def _stock(job: Path, w: dict, prov_names: list[str], first: int,
           say) -> tuple[list[dict], list[str]]:  # fmt: skip
    """Up to MAX_STOCK candidates for one broll/still wish, numbered from c<first>."""
    kinds = ["video", "image"] if w["kind"] == "broll" else ["image"]
    orient, loc = orientation(job), LOCALES.get(read_job(job).get("lang") or "de", "de-DE")
    size = output_size(job)
    hits: list[tuple[str, StockHit]] = []
    errors: list[str] = []
    for kind in kinds:
        for name in prov_names:
            if len(hits) >= MAX_STOCK:
                break
            try:
                prov = get_provider(name)
                ok, why = prov.available()
                if not ok:
                    errors.append(f"{name}: {why}")
                    continue
                for h in prov.search(w["query"], kind, orient, loc, MAX_STOCK * 2):
                    if h.kind == "video" and (h.duration_s or 0) < 1.0:
                        continue
                    hits.append((name, h))
            except ProviderError as e:
                errors.append(str(e))
        if len(hits) >= MAX_STOCK:
            break
    # videos first for broll, then the rest; never twice the same item
    seen, picked = set(), []
    for name, h in hits:
        if h.id not in seen:
            seen.add(h.id)
            picked.append((name, h))
    out = []
    folder = job / DIR / str(w["n"])
    for k, (name, h) in enumerate(picked[:MAX_STOCK], first):
        cid = f"c{k}"
        ext = ".mp4" if h.kind == "video" else ".jpg"
        dest = folder / f"{cid}{ext}"
        try:
            get_provider(name).download(h, dest, size)
            meta = _probe(dest)
            dur = meta["duration"] if h.kind == "video" else None
            if dur and dur > MAX_CLIP_S:
                _trim(dest, MAX_CLIP_S)
                dur = MAX_CLIP_S
            thumb = folder / f"{cid}_sm.jpg"
            _thumb(dest, thumb, h.kind == "video")
        except (ProviderError, ToolError) as e:
            errors.append(f"{name} {h.id}: {e}")
            dest.unlink(missing_ok=True)
            continue
        out.append({
            "id": cid, "kind": h.kind, "file": f"{DIR}/{w['n']}/{dest.name}",
            "thumb": f"{DIR}/{w['n']}/{thumb.name}", "width": meta["width"],
            "height": meta["height"], "duration_s": round(dur, 2) if dur else None,
            "source": name, "source_url": h.source_url, "stock_id": h.id, "author": h.author,
            "licence": h.licence, "attribution": h.attribution, "ai_generated": False,
            "model": None, "prompt": None, "seed": None, "watermark": "none",
            "sha256": _sha(dest), "fetched": now(), "hdr": _hdr(meta["info"]),
        })  # fmt: skip
    say(f'  wish {w["n"]}: {len(out)} stock candidate(s) for "{w["query"]}"')
    if not out:
        errors.append(f'wish {w["n"]}: no stock material found for "{w["query"]}"')
    return out, errors


def _hdr(info: dict) -> bool:
    from ._color import is_hdr

    return is_hdr(info)


def provider_name() -> str:
    return str(settings.load().get("image_provider") or "none")


def budget() -> float:
    try:
        return float(settings.load().get("material_budget_usd"))
    except (TypeError, ValueError):
        return 1.0


def _generate(job: Path, w: dict, count: int, first: int, data: dict, say) -> tuple[list, list]:
    name = provider_name()
    if name == "none":
        return [], ["no image generator is set (Settings → Material)"]
    try:
        prov = get_provider(name)
    except ProviderError as e:
        return [], [str(e)]
    ok, why = prov.available()
    if not ok:
        return [], [f"{name}: {why}"]
    ow, oh = output_size(job)
    est = prov.estimate_usd(ow, oh) * count
    spent = float(data.get("cost_usd") or 0)
    limit = budget()
    if spent + est > limit + 1e-9:
        return [], [
            f"generating {count} image(s) would cost about ${est:.2f}; this job has used "
            f"${spent:.2f} of its ${limit:.2f} limit (Settings → Material → cost limit)"
        ]
    out, errors = [], []
    folder = job / DIR / str(w["n"])
    pal = palette(job)
    for k in range(first, first + count):
        cid = f"c{k}"
        seed = random.randint(1, 2**31 - 1)
        try:
            res = prov.generate(w["prompt"], ow, oh, seed, pal, w.get("style") or "photo",
                                folder / cid)  # fmt: skip
            meta = _probe(res.file)
            thumb = folder / f"{cid}_sm.jpg"
            _thumb(res.file, thumb, False)
        except (ProviderError, ToolError) as e:
            errors.append(f"{name}: {e}")
            continue
        data["cost_usd"] = round(float(data.get("cost_usd") or 0) + (res.cost_usd or 0), 4)
        sha = _sha(res.file)
        out.append({
            "id": cid, "kind": "image", "file": f"{DIR}/{w['n']}/{res.file.name}",
            "thumb": f"{DIR}/{w['n']}/{thumb.name}", "width": meta["width"],
            "height": meta["height"], "duration_s": None, "source": name, "source_url": None,
            "author": None, "licence": "generated", "attribution": f"AI-generated ({res.model})",
            "ai_generated": True, "model": f"{res.model}@{res.revision}"
            if res.revision and res.revision != res.model else res.model,
            "prompt": w["prompt"], "seed": res.seed, "watermark": res.watermark,
            "sha256": sha, "source_sha256": sha, "fetched": now(),
            "cost_usd": res.cost_usd, "elapsed_s": res.elapsed_s, "hdr": False,
        })  # fmt: skip
    say(f"  wish {w['n']}: {len(out)} generated candidate(s) ({name})")
    return out, errors


def _next_id(cands: list[dict]) -> int:
    return 1 + max([int(c["id"][1:]) for c in cands if str(c.get("id", "")).startswith("c")
                    and c["id"][1:].isdigit()] or [0])  # fmt: skip


def fetch(job: Path, only: int | None = None, query: str | None = None,
          prompt: str | None = None, stock: bool = True, gen: bool = True,
          say=print) -> dict:  # fmt: skip
    """Fetch (and generate) candidates for every wish, or only wish `only`."""
    _job_material.human_only("fetches material (no network inside an agent run)")
    if os.environ.get("VIDEOCUT_AGENT"):
        raise MaterialError("no network inside an agent run")
    mode = _job_material.stored(read_job(job))["mode"]
    if mode == "none":
        raise MaterialError("the job has no material (job.json material mode none)")
    data = synced(job)
    if not data["wishes"]:
        raise MaterialError("the approved plan has no material wishes (or is not approved)")
    targets = [w for w in data["wishes"] if only is None or w["n"] == only]
    if not targets:
        raise MaterialError(f"no material wish {only} in the approved plan")
    t0 = time.monotonic()
    conf = settings.load()
    stock_names = [n for n in conf.get("stock_providers") or [] if n in ("pexels", "coverr")]
    errors: list[str] = []
    n_stock = n_gen = 0
    cost_before = float(data.get("cost_usd") or 0)
    say(f"material: {len(targets)} wish(es), mode {mode}")
    for w in targets:
        if query is not None:
            w["query"], w["query_edited"] = " ".join(query.split())[:200], True
        if prompt is not None:
            w["prompt"], w["prompt_edited"] = " ".join(prompt.split())[:PROMPT_MAX], True
        old = w.get("candidates", [])
        do_stock = stock and mode in ("stock", "both") and w["kind"] in ("broll", "still")
        do_gen = gen and mode in ("generated", "both") and w["kind"] in ("still", "icon")
        # A redo of one part keeps the other part's candidates, and a part that finds nothing
        # keeps its old ones. New ones are numbered after every old id, so a new download
        # never overwrites (or, failing, deletes) a file that is still listed.
        old_stock = [c for c in old if not c.get("ai_generated")]
        old_gen = [c for c in old if c.get("ai_generated")]
        new_stock, new_gen = old_stock, old_gen
        first = _next_id(old)
        if do_stock and w.get("query"):
            got, e = _stock(job, w, stock_names, first, say)
            new_stock = got or old_stock
            first += len(got)
            n_stock += len(got)
            errors += e
        if do_gen and w.get("prompt"):
            got, e = _generate(job, w, MAX_GEN, max(first, _next_id(old)), data, say)
            new_gen = got or old_gen
            n_gen += len(got)
            errors += e
        cands = new_stock + new_gen
        w["candidates"] = cands
        _clean(job / DIR / str(w["n"]), {c["id"] for c in cands})
        save(job, data)
    wall = round(time.monotonic() - t0, 1)
    spent = round(float(data.get("cost_usd") or 0) - cost_before, 4)
    data.setdefault("runs", []).append({"at": now(), "wishes": [w["n"] for w in targets],
                                        "stock": n_stock, "generated": n_gen, "cost_usd": spent,
                                        "wall_s": wall, "errors": errors[:20]})  # fmt: skip
    _mirror(job, data)
    with (job / "work" / "runs.jsonl").open("a") as f:
        f.write(json.dumps({"phase": "material", "wall_s": wall, "cost_usd": spent,
                            "stock": n_stock, "generated": n_gen}) + "\n")  # fmt: skip
    for e in errors:
        say(f"  ! {e}")
    if not n_stock and not n_gen and errors:
        raise MaterialError("no material found: " + "; ".join(errors[:3]))
    return {"wishes": len(targets), "stock": n_stock, "generated": n_gen, "cost_usd": spent,
            "cost_total_usd": data.get("cost_usd", 0), "wall_s": wall, "errors": errors,
            "pending": pending(job)}  # fmt: skip


# ------------------------------------------------------------------ show


def table(job: Path) -> list[str]:
    data = synced(job)
    dec = decisions(job)
    lines = []
    for w in data["wishes"]:
        n = w["n"]
        state = (f"chosen {dec[n]['id']}" if dec.get(n) else "none") if n in dec else "OPEN"
        lines.append(f"wish {n} [{w['kind']}, {w['src_from']:.1f}-{w['src_to']:.1f} s] {state}: "
                     f"{w.get('why') or ''}")  # fmt: skip
        lines.append(f"    query: {w.get('query') or '-'} | prompt: {w.get('prompt') or '-'}")
        for c in w.get("candidates", []):
            dur = f" {c['duration_s']:.1f}s" if c.get("duration_s") else ""
            ai = " AI" if c.get("ai_generated") else ""
            lines.append(
                f"    {c['id']}: {c['kind']}{dur} {c['width']}x{c['height']} "
                f"{c['source']}{ai} — {c.get('attribution') or ''}  {c['file']}"
            )
    return lines


def show(args) -> dict:
    job = resolve_job(args.job_ref or args.job)
    mode = _job_material.stored(read_job(job))["mode"]
    data = synced(job) if approved_wishes(job) else load(job)
    for line in table(job):
        print(line)
    left = pending(job)
    if left:
        print(f"choose per wish (human only): videocut tool material choose {job.name} <n> "
              "<candidate id|none>")  # fmt: skip
    ch = chosen(job)
    return {
        "mode": mode,
        "wishes": len(data["wishes"]),
        "candidates": sum(len(w.get("candidates", [])) for w in data["wishes"]),
        "pending": left,
        "chosen": [
            {
                "n": n,
                "id": c["id"],
                "kind": c["kind"],
                "file": c["file"],
                "wish_kind": c["wish_kind"],
                "src_from": c["src_from"],
                "src_to": c["src_to"],
                "duration_s": c.get("duration_s"),
                "ai_generated": c.get("ai_generated"),
                "style": c.get("style"),
            }
            for n, c in sorted(ch.items())
        ],  # fmt: skip
        "cost_usd": data.get("cost_usd", 0),
    }


# ------------------------------------------------------------------ CLI


def fetch_cmd(args) -> dict:
    job = resolve_job(args.job_ref or args.job)
    return fetch(job, args.wish, args.query, args.prompt)


def generate_cmd(args) -> dict:
    job = resolve_job(args.job_ref or args.job)
    return fetch(job, args.wish, None, args.prompt, stock=False)


def choose_cmd(args) -> dict:
    _job_material.human_only("chooses material")
    job = resolve_job(args.job_ref)
    cand = None if args.candidate.lower() == "none" else args.candidate
    return choose(job, args.n, cand)


def remove_files(job: Path) -> None:
    """Drop the fetched material (a new plan with other wishes)."""
    shutil.rmtree(job / DIR, ignore_errors=True)
    (job / FILE).unlink(missing_ok=True)


def register(subparsers) -> None:
    p = subparsers.add_parser("material", help="stock B-roll and generated stills (host only)")
    sub = p.add_subparsers(dest="material_cmd", required=True)
    f = sub.add_parser("fetch", help="search and download candidates for the plan's wishes",
                       parents=[job_parent()])  # fmt: skip
    f.add_argument("job_ref", nargs="?", metavar="job", help="job folder or id")
    f.add_argument("--wish", type=int, help="only this wish")
    f.add_argument("--query", help="with --wish: the edited stock query")
    f.add_argument("--prompt", help="with --wish: the edited generator prompt")
    f.set_defaults(handler=run_tool("material fetch", fetch_cmd))
    g = sub.add_parser("generate", help="generate images for still/icon wishes",
                       parents=[job_parent()])  # fmt: skip
    g.add_argument("job_ref", nargs="?", metavar="job", help="job folder or id")
    g.add_argument("--wish", type=int, help="only this wish")
    g.add_argument("--prompt", help="with --wish: the edited prompt")
    g.set_defaults(handler=run_tool("material generate", generate_cmd))
    c = sub.add_parser("choose", help="choose a candidate for a wish, or none (human only)")
    c.add_argument("job_ref", metavar="job", help="job folder or id")
    c.add_argument("n", type=int, help="the wish number")
    c.add_argument("candidate", help="candidate id (c1, c2, ...) or none")
    c.set_defaults(handler=run_tool("material choose", choose_cmd))
    s = sub.add_parser("show", help="wishes, candidates and choices", parents=[job_parent()])
    s.add_argument("job_ref", nargs="?", metavar="job", help="job folder or id")
    s.set_defaults(handler=run_tool("material show", show))
