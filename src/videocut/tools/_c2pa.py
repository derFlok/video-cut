"""C2PA provenance through the pinned `c2patool` (manifest/tools.json, contentauth/c2pa-rs).

    sign(src, dst, ai_items, version)  the finished reel gets a C2PA manifest: claim
             generator "Video Cut <version>", one c2pa.actions assertion with
             c2pa.created (the reel: digitalSourceType compositeCapture, or
             compositeWithTrainedAlgorithmicMedia when AI material is in it), c2pa.edited
             (the cut) and per AI item a c2pa.created with digitalSourceType
             trainedAlgorithmicMedia and the model as softwareAgent. Nothing personal: no
             names, no prompts, no paths (prompts stay in work/disclosure.json). No
             timestamp authority (no network): the claim carries no trusted time.
    read(path)    what `c2patool <file>` reports, reduced to the validation result
    inspect(path) whether a file (a generated still) carries a C2PA manifest or IPTC
             "trainedAlgorithmicMedia" metadata; for work/material.json

Signing key: a local certificate chain created once with macOS openssl in
$VIDEOCUT_HOME/state/c2pa/ (a root "Video Cut local" plus a signer certificate it issued;
C2PA refuses a self-signed end certificate). No validator trusts that root, so they show the
manifest as valid but from an unknown signer ("signingCredential.untrusted"); a certificate
from a trusted issuer is a later step.

A missing c2patool is not an error: sign() answers {"c2pa": "skipped"} and the reel is
delivered without a manifest.
"""

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..paths import BIN, state_dir, tool_env

TOOL = "c2patool"
OPENSSL = "/usr/bin/openssl"
IPTC = "http://cv.iptc.org/newscodes/digitalsourcetype/"
AI_SOURCE = IPTC + "trainedAlgorithmicMedia"
CERT_DAYS = 3650
TIMEOUT_S = 600


def binary() -> Path | None:
    """The installed c2patool (.tools/bin, else PATH), or None."""
    p = BIN / TOOL
    if p.is_file():
        return p
    found = shutil.which(TOOL)
    return Path(found) if found else None


def version() -> str | None:
    exe = binary()
    if exe is None:
        return None
    try:
        out = subprocess.run([str(exe), "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    words = out.stdout.split()
    return words[-1] if out.returncode == 0 and words else None


def cert_dir() -> Path:
    return state_dir() / "c2pa"


def _openssl(*args: str, stdin: str | None = None) -> None:
    out = subprocess.run([OPENSSL, *args], capture_output=True, text=True, input=stdin)
    if out.returncode != 0:
        raise RuntimeError(f"openssl {args[0]} failed: {out.stderr.strip()[-300:]}")


def ensure_cert() -> tuple[Path, Path]:
    """(signer private key in PKCS#8, certificate chain signer+root), created once. Made in
    a private temp folder (umask 077) under a lock, then moved into place, so two first
    renders never mix their files and no key is ever readable by others."""
    d = cert_dir()
    key, chain = d / "signer.pk8", d / "chain.pem"
    if key.is_file() and chain.is_file():
        return key, chain
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    old_mask = os.umask(0o077)
    try:
        with (d / ".lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if key.is_file() and chain.is_file():  # made by the run we waited for
                return key, chain
            with tempfile.TemporaryDirectory(dir=d) as tmp_name:
                _make_chain(Path(tmp_name))
                (Path(tmp_name) / "root.pem").replace(d / "root.pem")
                (Path(tmp_name) / "signer.pem").replace(d / "signer.pem")
                (Path(tmp_name) / "signer.pk8").replace(key)
                (Path(tmp_name) / "chain.pem").replace(chain)  # last: marks it complete
    finally:
        os.umask(old_mask)
    return key, chain


def _make_chain(tmp: Path) -> None:
    """root.pem, signer.pem, signer.pk8 and chain.pem in `tmp`; the root key stays in `tmp`
    and goes with it (nothing else is ever issued)."""
    ca_ext = tmp / "ca.cnf"
    ca_ext.write_text(
        "[req]\ndistinguished_name=dn\n[dn]\n[v3_ca]\nbasicConstraints=critical,CA:TRUE\n"
        "keyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n"
    )
    ee_ext = tmp / "signer.ext"
    ee_ext.write_text(
        "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
        "extendedKeyUsage=emailProtection\nsubjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid\n"
    )
    ca_key, ca_pem = tmp / "root.key", tmp / "root.pem"
    ee_key, ee_csr, ee_pem = tmp / "signer.key", tmp / "signer.csr", tmp / "signer.pem"
    subj = "/O=Video Cut local/CN=Video Cut local "
    _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(ca_key))
    _openssl("req", "-x509", "-new", "-key", str(ca_key), "-sha256", "-days", str(CERT_DAYS),
             "-subj", subj + "root", "-config", str(ca_ext), "-extensions", "v3_ca",
             "-out", str(ca_pem))  # fmt: skip
    _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(ee_key))
    _openssl("pkcs8", "-topk8", "-nocrypt", "-in", str(ee_key), "-out", str(tmp / "signer.pk8"))
    _openssl("req", "-new", "-key", str(ee_key), "-subj", subj + "signer", "-out", str(ee_csr))
    _openssl("x509", "-req", "-in", str(ee_csr), "-CA", str(ca_pem), "-CAkey", str(ca_key),
             "-CAcreateserial", "-days", str(CERT_DAYS), "-sha256", "-extfile", str(ee_ext),
             "-out", str(ee_pem))  # fmt: skip
    (tmp / "chain.pem").write_text(ee_pem.read_text() + ca_pem.read_text())


def manifest(ai_items: list[dict], version: str, title: str) -> dict:
    """The manifest definition (without the signing fields). `ai_items`: {"model"} each;
    one c2pa.created per distinct model."""
    agent = {"name": "Video Cut", "version": version}
    models = sorted({str(i.get("model") or "unknown model") for i in ai_items})
    reel = "compositeWithTrainedAlgorithmicMedia" if ai_items else "compositeCapture"
    actions = [
        {"action": "c2pa.created", "digitalSourceType": IPTC + reel, "softwareAgent": agent},
        {"action": "c2pa.edited", "softwareAgent": agent},
        *(
            {"action": "c2pa.created", "digitalSourceType": AI_SOURCE,
             "softwareAgent": {"name": m}, "description": "AI-generated material"}
            for m in models
        ),
    ]  # fmt: skip
    return {
        "claim_generator_info": [agent],
        "title": title,
        "assertions": [{"label": "c2pa.actions", "data": {"actions": actions}}],
    }


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, env=tool_env(), timeout=TIMEOUT_S)


def read(path: Path) -> dict:
    """`c2patool <path>` reduced: {"state": "Valid"|"Invalid"|"Trusted"|None, "codes":
    [validation status codes], "generator", "actions": [action names],
    "digital_source_types": [...]} or {"state": None, "error"} when there is no manifest or
    the tool failed."""
    exe = binary()
    if exe is None:
        return {"state": None, "error": "c2patool is not installed"}
    try:
        out = _run([str(exe), str(path)])
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"state": None, "error": str(e)}
    try:
        doc = json.loads(out.stdout)
    except ValueError:
        return {"state": None, "error": (out.stderr or out.stdout).strip()[-300:] or "no manifest"}
    active = (doc.get("manifests") or {}).get(doc.get("active_manifest")) or {}
    actions = [
        a
        for asr in active.get("assertions") or []
        if str(asr.get("label", "")).startswith("c2pa.actions")
        for a in (asr.get("data") or {}).get("actions") or []
    ]
    gen = (active.get("claim_generator_info") or [{}])[0]
    return {
        "state": doc.get("validation_state"),
        "codes": [s.get("code") for s in doc.get("validation_status") or []],
        "generator": " ".join(str(gen[k]) for k in ("name", "version") if gen.get(k)) or None,
        "signer": (active.get("signature_info") or {}).get("issuer"),
        "actions": [a.get("action") for a in actions],
        "digital_source_types": sorted({a["digitalSourceType"] for a in actions
                                         if a.get("digitalSourceType")}),  # fmt: skip
        "manifest_sha256": hashlib.sha256(
            json.dumps(active, sort_keys=True).encode()
        ).hexdigest() if active else None,
    }  # fmt: skip


def valid(result: dict) -> bool:
    """A manifest whose only complaint is the unknown (local) signer."""
    codes = [c for c in result.get("codes") or [] if c != "signingCredential.untrusted"]
    return result.get("state") in ("Valid", "Trusted") and not codes


def sign(src: Path, dst: Path, ai_items: list[dict], version: str) -> dict:
    """Write `src` with a C2PA manifest to `dst` and verify it. Returns the render.json
    "c2pa" entry: {"c2pa": "signed", "verify": read(dst), "valid"} or {"c2pa": "skipped",
    "reason"} (no c2patool) or {"c2pa": "failed", "error"} (dst is not written)."""
    exe = binary()
    if exe is None:
        return {"c2pa": "skipped", "reason": "c2patool is not installed"}
    try:
        key, chain = ensure_cert()
    except (OSError, RuntimeError) as e:
        return {"c2pa": "failed", "error": f"signing certificate: {e}"}
    definition = {
        **manifest(ai_items, version, dst.name),
        "alg": "es256",
        "private_key": str(key),
        "sign_cert": str(chain),
    }
    mf = dst.with_name(dst.name + ".c2pa.json")
    try:
        mf.write_text(json.dumps(definition))
        out = _run([str(exe), str(src), "-m", str(mf), "-o", str(dst), "-f"])
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"c2pa": "failed", "error": str(e)}
    finally:
        mf.unlink(missing_ok=True)
    if out.returncode != 0 or not dst.is_file():
        dst.unlink(missing_ok=True)
        return {"c2pa": "failed", "error": (out.stderr or out.stdout).strip()[-300:]}
    result = read(dst)
    return {"c2pa": "signed", "valid": valid(result), "verify": result}


def _has_iptc_ai(path: Path) -> bool:
    """The file's bytes name the IPTC digital source type trainedAlgorithmicMedia (XMP)."""
    needle = b"trainedAlgorithmicMedia"
    with path.open("rb") as f:
        tail = b""
        while chunk := f.read(1 << 20):
            if needle in tail + chunk:
                return True
            tail = chunk[-len(needle) :]
    return False


def inspect(path: Path) -> dict:
    """Provenance an incoming file (a generated still) carries, for work/material.json:
    {"c2pa": "present"|"absent"|"unknown" (no c2patool), "state", "generator",
    "digital_source_types", "manifest_sha256", "iptc_ai": bool}. Keep the file itself
    unchanged next to any derived copy; nothing here strips metadata. SynthID is an
    invisible watermark no local tool reads: the provider's documentation is the source
    for that (material.json "watermark")."""
    path = Path(path)
    out = {"iptc_ai": _has_iptc_ai(path)}
    if binary() is None:
        return {**out, "c2pa": "unknown"}
    r = read(path)
    if r.get("state") is None:
        return {**out, "c2pa": "absent"}
    return {
        **out,
        "c2pa": "present",
        **{k: r.get(k) for k in ("state", "generator", "digital_source_types", "manifest_sha256")},
    }
