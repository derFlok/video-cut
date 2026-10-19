"""Which chosen material is on screen when, and where the AI label must show.

Read from work/material.json (`material fetch` / `generate` / `job material choose`) and the
EDL, nothing else:

    on_screen(job, edl, raw)  every chosen material item the cut shows, in output time:
        a segment with "material": <wish n> (layout "broll") over its output span
        (edl.output_spans), and an overlay with scene "still_card" and props.material
        <wish n> over its t0..t1. The candidate is the wish's "chosen" one (props.candidate
        of a still_card names another candidate of that wish: that one).
        -> [{"n", "kind": "broll"|"still_card", "t0", "t1", "wish", "candidate"}]
    labelled(item)  AI-generated material that must carry the visible label: every
        ai_generated candidate of style "photo" (anything that is not "illustration"
        counts as photo), and illustrations while settings "ai_label_illustrations"
        (default true) is on. The photo label cannot be switched off (EU AI Act Art. 50).
    badge_items(...)  the overlays.json items of scene "ai_badge": the labelled spans
        merged (gaps under MERGE_GAP_S close), text in the job language.

`videocut tool overlays` adds the badge items, verify checks they were drawn
(ai_label_missing), render and _disclosure list the AI items and the stock attributions.
"""

import json
from pathlib import Path

from .. import settings
from ..edl import Edl, output_spans

TEXT = {"de": "KI-generiert", "en": "AI-generated"}
MERGE_GAP_S = 0.5
SCENE = "ai_badge"
CARD_SCENES = ("still_card",)
GENERATORS = ("gemini", "openai", "local")  # material.json "source" of generated images


def read_material(job: Path) -> dict:
    """work/material.json, {} when missing or malformed."""
    try:
        doc = json.loads((job / "work" / "material.json").read_text())
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def _wishes(material: dict) -> dict[int, dict]:
    out = {}
    for w in material.get("wishes") or []:
        if isinstance(w, dict) and isinstance(w.get("n"), int) and not isinstance(w["n"], bool):
            out[w["n"]] = w
    return out


def _candidate(wish: dict, cid) -> dict | None:
    for c in wish.get("candidates") or []:
        if isinstance(c, dict) and c.get("id") == cid and cid is not None:
            return c
    return None


def chosen_wishes(material: dict) -> list[dict]:
    """Every wish with a chosen candidate, that candidate under "_chosen"."""
    out = []
    for w in _wishes(material).values():
        c = _candidate(w, w.get("chosen"))
        if c is not None:
            out.append({**w, "_chosen": c})
    return out


def on_screen(job: Path, edl: Edl, raw: dict, material: dict | None = None) -> list[dict]:
    """Every chosen material item the cut shows (see the module doc), in output time order.
    `raw` is the edl.json document `edl` was loaded from (segments in the same order)."""
    wishes = _wishes(read_material(job) if material is None else material)
    if not wishes:
        return []
    out = []
    segs = raw.get("segments") if isinstance(raw.get("segments"), list) else []
    for k, (t0, t1) in enumerate(output_spans(edl)):
        s = segs[k] if k < len(segs) and isinstance(segs[k], dict) else {}
        n = s.get("material")
        wish = wishes.get(n) if isinstance(n, int) and not isinstance(n, bool) else None
        cand = _candidate(wish, wish.get("chosen")) if wish else None
        if cand is not None:
            out.append({"n": n, "kind": "broll", "t0": round(float(t0), 6),
                        "t1": round(float(t1), 6), "wish": wish, "candidate": cand})  # fmt: skip
    for o in raw.get("overlays") or []:
        if not isinstance(o, dict) or o.get("scene") not in CARD_SCENES:
            continue
        props = o.get("props") if isinstance(o.get("props"), dict) else {}
        n, t0, t1 = props.get("material"), o.get("t0"), o.get("t1")
        if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in (n, t0, t1)):
            continue
        wish = wishes.get(n)
        if wish is None:
            continue
        cand = _candidate(wish, props.get("candidate")) or _candidate(wish, wish.get("chosen"))
        if cand is not None:
            out.append({"n": n, "kind": o["scene"], "t0": float(t0), "t1": float(t1),
                        "wish": wish, "candidate": cand})  # fmt: skip
    return sorted(out, key=lambda i: (i["t0"], i["n"]))


def is_ai(item: dict) -> bool:
    """ai_generated true, or (failing closed on a candidate whose flag was lost or changed)
    a generator source or the "generated" licence."""
    c = item["candidate"]
    return (
        c.get("ai_generated") is True
        or c.get("source") in GENERATORS
        or c.get("licence") == "generated"
    )


def style(item: dict) -> str:
    s = item["candidate"].get("style") or item["wish"].get("style")
    return "illustration" if s == "illustration" else "photo"


def illustrations_labelled() -> bool:
    try:
        return settings.load().get("ai_label_illustrations", True) is not False
    except (OSError, ValueError):
        return True


def labelled(item: dict, illustrations: bool | None = None) -> bool:
    """Whether this item must carry the visible AI label."""
    if not is_ai(item):
        return False
    if style(item) == "photo":
        return True
    return illustrations_labelled() if illustrations is None else illustrations


def spans(items: list[dict], gap: float = MERGE_GAP_S) -> list[dict]:
    """The items' [t0, t1] merged where they touch or are less than `gap` apart:
    [{"t0", "t1", "material": [wish n, ...]}]."""
    out: list[dict] = []
    for i in sorted(items, key=lambda i: i["t0"]):
        if out and i["t0"] - out[-1]["t1"] < gap:
            out[-1]["t1"] = max(out[-1]["t1"], i["t1"])
            if i["n"] not in out[-1]["material"]:
                out[-1]["material"].append(i["n"])
        else:
            out.append({"t0": i["t0"], "t1": i["t1"], "material": [i["n"]]})
    return out


def text(lang: str) -> str:
    return TEXT.get(lang, TEXT["en"])


def badge_items(items: list[dict], lang: str, illustrations: bool | None = None) -> list[dict]:
    """overlays.json items (scene "ai_badge") for every labelled item of `items`."""
    need = [i for i in items if labelled(i, illustrations)]
    return [
        {"scene": SCENE, "t0": round(s["t0"], 6), "t1": round(s["t1"], 6),
         "props": {"text": text(lang), "material": s["material"]}}
        for s in spans(need)
    ]  # fmt: skip


def models(items: list[dict]) -> list[str]:
    """The distinct generator models of the AI items, in first-shown order."""
    out: list[str] = []
    for i in items:
        if is_ai(i):
            m = str(i["candidate"].get("model") or i["candidate"].get("source") or "unknown model")
            if m not in out:
                out.append(m)
    return out
