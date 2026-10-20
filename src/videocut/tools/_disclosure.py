"""What to disclose when the video is published: work/disclosure.json + work/disclosure.md.

Written by `videocut tool render` (full render, host side) from work/material.json and the
EDL (_ai_label.on_screen), never by the agent:

    {"version": 1, "lang", "ai": [{"n", "kind", "t0", "t1" (output s), "model", "prompt",
       "style", "source", "watermark", "labelled"}],
     "stock": [{"n", "source", "author", "licence", "attribution", "attribution_full",
       "licence_url", "source_url"}] (one per chosen stock candidate shown; attribution_full
       is the licence's full credit line where it asks for one, CC BY: title, creator,
       licence and its link; it is printed with the source link instead of the short one),
     "platforms": [{"id": "youtube"|"meta"|"tiktok", "label", "status": "required"|"not needed",
       "why"}],
     "snippet": description text in the job language (AI note + attributions; "" when there
       is nothing to disclose),
     "comment": the container comment render wrote (or null), "c2pa": render's c2pa result}

Platform rules: realistic AI material (style "photo") needs the platform's own AI label on
YouTube ("Altered or synthetic content"), Meta ("AI info") and TikTok ("AI-generated
content"); a clearly drawn illustration does not. Prompts stay here and never go into the
video file or its C2PA manifest.
"""

import json
from pathlib import Path

from ..edl import Edl
from . import _ai_label
from .common import write_json

PLATFORMS = (
    ("youtube", {"de": "YouTube: „Veränderte oder synthetische Inhalte“ auf „Ja“",
                 "en": "YouTube: “Altered or synthetic content” set to “Yes”"}),
    ("meta", {"de": "Meta (Instagram, Facebook): „KI-Info“ einschalten",
              "en": "Meta (Instagram, Facebook): turn on “AI info”"}),
    ("tiktok", {"de": "TikTok: „KI-generierte Inhalte“ einschalten",
                "en": "TikTok: turn on “AI-generated content”"}),
)  # fmt: skip
WHY = {
    "photo": {"de": "Das Video zeigt realistisch wirkendes KI-Bildmaterial.",
              "en": "The video shows realistic AI-generated imagery."},
    "illustration": {"de": "Nur gezeichnete KI-Illustrationen: die Plattformen verlangen das "
                           "Label nur für realistisch wirkendes Material.",
                     "en": "Only drawn AI illustrations: the platforms ask for the label "
                           "only for realistic material."},
    "none": {"de": "Kein KI-generiertes Bildmaterial im Video.",
             "en": "No AI-generated imagery in the video."},
}  # fmt: skip
SNIPPET = {
    "ai": {"de": "Hinweis: Dieses Video enthält KI-generiertes Bildmaterial ({models}).",
           "en": "Note: this video contains AI-generated imagery ({models})."},
    "stock": {"de": "Bildnachweise: {items}.", "en": "Credits: {items}."},
}  # fmt: skip
COMMENT = "Contains AI-generated material: {models}"
STOCK_KEYS = ("source", "author", "licence", "attribution", "attribution_full", "licence_url",
              "source_url")  # fmt: skip
SOURCE = {"de": "Quelle", "en": "Source"}


def _t(table: dict, lang: str) -> str:
    return table.get(lang, table["en"])


def _full_credit(s: dict, lang: str) -> str:
    """The licence's full credit line with the source link (CC BY), "" when there is none."""
    full = str(s.get("attribution_full") or "").strip()
    if not full:
        return ""
    url = s.get("source_url")
    return f"{full.rstrip('.')}. {_t(SOURCE, lang)}: {url}" if url else full


def comment(items: list[dict]) -> str | None:
    """The container `comment` for the shown material, None without AI items."""
    models = _ai_label.models(items)
    return COMMENT.format(models=", ".join(models)) if models else None


def build(items: list[dict], lang: str, provenance: dict | None = None) -> dict:
    """The disclosure document for the material items the cut shows (_ai_label.on_screen)."""
    ai, stock, seen = [], [], set()
    for i in items:
        c = i["candidate"]
        if _ai_label.is_ai(i):
            ai.append({
                "n": i["n"], "kind": i["kind"], "t0": round(i["t0"], 3), "t1": round(i["t1"], 3),
                "model": c.get("model") or c.get("source"), "prompt": c.get("prompt")
                or i["wish"].get("prompt"), "style": _ai_label.style(i),
                "source": c.get("source"), "watermark": c.get("watermark"),
                "labelled": _ai_label.labelled(i),
            })  # fmt: skip
        elif (i["n"], c.get("id")) not in seen:
            seen.add((i["n"], c.get("id")))
            stock.append({k: c.get(k) for k in STOCK_KEYS} | {"n": i["n"]})
    kind = "photo" if any(a["style"] == "photo" for a in ai) else (
        "illustration" if ai else "none")  # fmt: skip
    platforms = [
        {"id": pid, "label": _t(label, lang),
         "status": "required" if kind == "photo" else "not needed", "why": _t(WHY[kind], lang)}
        for pid, label in PLATFORMS
    ]  # fmt: skip
    parts = []
    if ai:
        parts.append(_t(SNIPPET["ai"], lang).format(models=", ".join(_ai_label.models(items))))
    credits = [_full_credit(s, lang) or s["attribution"] or s["author"] or s["source"]
               for s in stock]  # fmt: skip
    credits = [str(c) for c in credits if c]
    if credits:
        parts.append(_t(SNIPPET["stock"], lang).format(items="; ".join(credits)))
    prov = provenance or {}
    return {
        "version": 1,
        "lang": lang,
        "ai": ai,
        "stock": stock,
        "platforms": platforms,
        "snippet": "\n".join(parts),
        "comment": prov.get("comment"),
        "c2pa": prov.get("c2pa"),
    }


MD = {
    "title": {"de": "Veröffentlichen: Kennzeichnung und Nachweise",
              "en": "Publishing: labels and credits"},
    "nothing": {"de": "Kein KI-generiertes und kein Stock-Material im Video: nichts zu "
                      "kennzeichnen.",
                "en": "No AI-generated and no stock material in the video: nothing to "
                      "disclose."},
    "platforms": {"de": "Plattformen", "en": "Platforms"},
    "required": {"de": "nötig", "en": "required"},
    "not needed": {"de": "nicht nötig", "en": "not needed"},
    "snippet": {"de": "Text für die Beschreibung", "en": "Description text"},
    "ai": {"de": "KI-generiertes Material", "en": "AI-generated material"},
    "stock": {"de": "Stock-Material", "en": "Stock material"},
    "prompt": {"de": "Prompt", "en": "Prompt"},
}  # fmt: skip


def markdown(doc: dict) -> str:
    lang = doc["lang"]
    out = [f"# {_t(MD['title'], lang)}", ""]
    if not doc["ai"] and not doc["stock"]:
        return "\n".join([*out, _t(MD["nothing"], lang), ""])
    out += [f"## {_t(MD['platforms'], lang)}", ""]
    for p in doc["platforms"]:
        out.append(f"- [{'x' if p['status'] == 'required' else ' '}] {p['label']}: "
                   f"{_t(MD[p['status']], lang)} ({p['why']})")  # fmt: skip
    if doc["snippet"]:
        out += ["", f"## {_t(MD['snippet'], lang)}", "", doc["snippet"]]
    if doc["ai"]:
        out += ["", f"## {_t(MD['ai'], lang)}", ""]
        for a in doc["ai"]:
            prompt = f"; {_t(MD['prompt'], lang)}: {a['prompt']}" if a["prompt"] else ""
            out.append(f"- {a['t0']:.1f}–{a['t1']:.1f} s: {a['model']} ({a['style']}){prompt}")
    if doc["stock"]:
        out += ["", f"## {_t(MD['stock'], lang)}", ""]
        for s in doc["stock"]:
            if full := _full_credit(s, lang):
                out.append(f"- {full}")
                continue
            line = s["attribution"] or s["author"] or s["source"]
            out.append(f"- {line}" + (f" ({s['licence']})" if s["licence"] else "")
                       + (f" {s['source_url']}" if s["source_url"] else ""))  # fmt: skip
    return "\n".join([*out, ""])


def write(job: Path, edl: Edl, raw: dict, lang: str, provenance: dict | None = None) -> dict:
    """Write work/disclosure.json and work/disclosure.md; returns the document."""
    doc = build(_ai_label.on_screen(job, edl, raw), lang, provenance)
    write_json(job / "work" / "disclosure.json", doc)
    (job / "work" / "disclosure.md").write_text(markdown(doc))
    return doc


def read(job: Path) -> dict | None:
    try:
        doc = json.loads((job / "work" / "disclosure.json").read_text())
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None
