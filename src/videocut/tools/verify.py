"""`videocut tool verify` (STUB, owned by the verify worker).

Check sheets at every cut point (+-2 frames) and caption change of the last render.
"""

from .common import job_parent, not_implemented


def register(subparsers) -> None:
    p = subparsers.add_parser("verify", help="check sheets at every cut", parents=[job_parent()])
    p.set_defaults(handler=not_implemented("verify"))
