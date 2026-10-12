"""`videocut tool sheet contact|burst|timeline` (STUB, owned by the sheets worker).

contact:   3x3 contact sheets over the proxy, one tile every N seconds
burst:     frames around source time t (+-span) with timestamp and frame number
timeline:  filmstrip + waveform + word labels for [t0, t1]
"""

from .common import job_parent, not_implemented


def register(subparsers) -> None:
    p = subparsers.add_parser("sheet", help="contact, burst and timeline sheets")
    sub = p.add_subparsers(dest="sheet_cmd", required=True)
    c = sub.add_parser("contact", help="3x3 contact sheets", parents=[job_parent()])
    c.add_argument("--every", type=float, default=2.0, metavar="S", help="seconds between tiles")
    c.set_defaults(handler=not_implemented("sheet contact"))
    b = sub.add_parser("burst", help="frames around one moment", parents=[job_parent()])
    b.add_argument("t", type=float, help="source time in seconds")
    b.add_argument("--span", type=float, default=0.5, help="seconds before and after t")
    b.set_defaults(handler=not_implemented("sheet burst"))
    t = sub.add_parser("timeline", help="filmstrip + waveform + words", parents=[job_parent()])
    t.add_argument("t0", type=float)
    t.add_argument("t1", type=float)
    t.set_defaults(handler=not_implemented("sheet timeline"))
