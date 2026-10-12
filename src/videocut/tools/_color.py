"""HDR -> SDR for everything the pipeline encodes (proxy, reel).

Phones record HLG (iPhone) or PQ in BT.2020, 10 bit. Re-encoding that to 8-bit H.264
without tonemapping keeps the HDR tags on SDR-range pixels and looks washed out. HDR
sources are linearised with zscale, tonemapped (mobius: keeps SDR-range
brightness close to the original, unlike hable; no desaturation) and converted to
BT.709 limited range; SDR sources pass through unchanged.
"""

HDR_TRANSFERS = {"arib-std-b67", "smpte2084"}  # HLG, PQ
# Input properties zscale falls back to when the stream does not tag them.
_DEFAULT_PRIMARIES, _DEFAULT_MATRIX = "bt2020", "bt2020nc"
_KNOWN_PRIMARIES = {"bt2020", "bt709"}
_KNOWN_MATRICES = {"bt2020nc", "bt2020c", "bt709"}

# Output tags for a tonemapped encode (ffmpeg output options).
SDR_TAGS = [
    "-color_primaries",
    "bt709",
    "-color_trc",
    "bt709",
    "-colorspace",
    "bt709",
    "-color_range",
    "tv",
]


def is_hdr(info: dict) -> bool:
    """`info` is a probe dict (work/probe.json)."""
    return info.get("color_transfer") in HDR_TRANSFERS


def to_sdr(info: dict) -> str:
    """Filter chain (no labels) that turns this source's frames into SDR BT.709 yuv420p.
    Empty for SDR sources."""
    if not is_hdr(info):
        return ""
    prim = info.get("color_primaries")
    prim = prim if prim in _KNOWN_PRIMARIES else _DEFAULT_PRIMARIES
    matrix = info.get("color_space")
    matrix = matrix if matrix in _KNOWN_MATRICES else _DEFAULT_MATRIX
    return ",".join(
        [
            f"zscale=tin={info['color_transfer']}:pin={prim}:min={matrix}:rin=tv:t=linear:npl=100",
            "format=gbrpf32le",
            "zscale=p=bt709",
            "tonemap=tonemap=mobius:desat=0",
            "zscale=t=bt709:m=bt709:r=tv",
            "format=yuv420p",
        ]
    )


def output_tags(info: dict) -> list[str]:
    return SDR_TAGS if is_hdr(info) else []
