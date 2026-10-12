"""`videocut tool transcribe|candidates` (STUB, owned by the transcription worker).

transcribe:  work/audio.wav -> work/words.json (word timestamps) + work/takes_packed.md
candidates:  filler-word candidates from gaps with voice energy -> work/candidates.json
"""

from .common import job_parent, not_implemented


def register(subparsers) -> None:
    t = subparsers.add_parser(
        "transcribe", help="local transcription with word timestamps", parents=[job_parent()]
    )
    t.add_argument("--model", choices=["turbo", "parakeet"], default="turbo")
    t.set_defaults(handler=not_implemented("transcribe"))
    c = subparsers.add_parser(
        "candidates", help="filler-word candidates between words", parents=[job_parent()]
    )
    c.set_defaults(handler=not_implemented("candidates"))
