"""The slide templates of the shared contract: names, content fields and text limits, for
plan.json validation (tools/plan.py). `videocut tool slides check` is the authority for
work/slides.json; this module checks the plan's copy the same way.

    errors(template, content)  problems as JSON-path suffixes (".content.items[2]: ...")
"""

import re

ID_RE = re.compile(r"[a-z][a-z0-9_-]{0,19}")
LAYOUTS = ("slide", "slide_split", "slide_full")
TEMPLATES = (
    "title", "bullets", "big_number", "compare", "steps", "timeline", "bar_chart", "quote",
    "cta", "image",
)  # fmt: skip
# Plain-word names for the plan gate (the app has its own translations).
NAMES = {
    "title": "Title", "bullets": "Bullet points", "big_number": "Big number",
    "compare": "Comparison", "steps": "Steps", "timeline": "Timeline",
    "bar_chart": "Bar chart", "quote": "Quote", "cta": "Call to action",
    "image": "Your slide",
}  # fmt: skip
ASSET_RE = re.compile(r"input/slides/page_\d{2}\.png")


def _text(v, limit: int, p: str, required: bool = True) -> list[str]:
    if v is None and not required:
        return []
    if not (isinstance(v, str) and v.strip()):
        return [f"{p}: must be a non-empty string"]
    if len(v) > limit:
        return [f"{p}: {len(v)} characters, at most {limit}"]
    return []


def _texts(v, lo: int, hi: int, limit: int, p: str) -> list[str]:
    if not (isinstance(v, list) and lo <= len(v) <= hi):
        return [f"{p}: must be a list of {lo}-{hi} texts"]
    return [e for i, x in enumerate(v) for e in _text(x, limit, f"{p}[{i}]")]


def _objs(v, lo: int, hi: int, p: str, check) -> list[str]:
    if not (isinstance(v, list) and lo <= len(v) <= hi):
        return [f"{p}: must be a list of {lo}-{hi} items"]
    errs = []
    for i, x in enumerate(v):
        q = f"{p}[{i}]"
        errs += check(x, q) if isinstance(x, dict) else [f"{q}: must be an object"]
    return errs


def _number(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and v == v and abs(v) < 1e15


def _content(t: str, c: dict, p: str) -> list[str]:
    if t == "title":
        return _text(c.get("title"), 60, f"{p}.title") + _text(
            c.get("subtitle"), 90, f"{p}.subtitle", False
        )
    if t == "bullets":
        return _text(c.get("title"), 60, f"{p}.title", False) + _texts(
            c.get("items"), 2, 5, 70, f"{p}.items"
        )
    if t == "big_number":
        errs = _text(c.get("value"), 12, f"{p}.value") + _text(c.get("label"), 70, f"{p}.label")
        if "count" in c and not isinstance(c["count"], bool):
            errs.append(f"{p}.count: must be true or false")
        return errs
    if t == "compare":

        def side(x, q):
            return _text(x.get("title"), 40, f"{q}.title") + _texts(
                x.get("items"), 1, 4, 60, f"{q}.items"
            )

        errs = []
        for k in ("left", "right"):
            v = c.get(k)
            errs += side(v, f"{p}.{k}") if isinstance(v, dict) else [f"{p}.{k}: must be an object"]
        return errs
    if t == "steps":
        return _text(c.get("title"), 60, f"{p}.title", False) + _texts(
            c.get("steps"), 2, 5, 50, f"{p}.steps"
        )
    if t == "timeline":

        def point(x, q):
            return _text(x.get("label"), 16, f"{q}.label") + _text(x.get("text"), 50, f"{q}.text")

        return _text(c.get("title"), 60, f"{p}.title", False) + _objs(
            c.get("points"), 2, 6, f"{p}.points", point
        )

    if t == "bar_chart":

        def bar(x, q):
            errs = _text(x.get("label"), 24, f"{q}.label")
            return errs + ([] if _number(x.get("value")) else [f"{q}.value: must be a number"])

        return (
            _text(c.get("title"), 60, f"{p}.title", False)
            + _text(c.get("unit"), 12, f"{p}.unit", False)
            + _objs(c.get("bars"), 2, 6, f"{p}.bars", bar)
        )
    if t == "quote":
        return _text(c.get("text"), 160, f"{p}.text") + _text(
            c.get("author"), 50, f"{p}.author", False
        )
    if t == "cta":
        return (
            _text(c.get("title"), 50, f"{p}.title")
            + _text(c.get("subtitle"), 90, f"{p}.subtitle", False)
            + _text(c.get("url"), 60, f"{p}.url", False)
        )
    # image
    a = c.get("asset")
    return [] if isinstance(a, str) and ASSET_RE.fullmatch(a) else [
        f'{p}.asset: must be an uploaded page "input/slides/page_NN.png"'
    ]  # fmt: skip


def errors(template, content) -> list[str]:
    if template not in TEMPLATES:
        return [f".template: must be one of {', '.join(TEMPLATES)}"]
    if not isinstance(content, dict):
        return [".content: must be an object"]
    return _content(template, content, ".content")
