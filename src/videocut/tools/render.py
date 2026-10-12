"""`videocut tool render [--preview]` (STUB, owned by the render worker).

work/edl.json -> out/ (cut, crop/zoom, fades, loudnorm); --preview renders fast and small.
"""

from .common import job_parent, not_implemented


def register(subparsers) -> None:
    p = subparsers.add_parser("render", help="render work/edl.json", parents=[job_parent()])
    p.add_argument("--preview", action="store_true", help="fast low-resolution render")
    p.set_defaults(handler=not_implemented("render"))
