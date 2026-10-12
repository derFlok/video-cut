import hashlib

import pytest

from videocut import download


def test_checksum_mismatch_leaves_nothing_behind(tmp_path, monkeypatch):
    payload = b"hello"

    class Resp:
        headers = {"Content-Length": str(len(payload))}

        def __init__(self):
            self._data = [payload]

        def read(self, _n):
            return self._data.pop() if self._data else b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(download.urllib.request, "urlopen", lambda *a, **k: Resp())
    dest = tmp_path / "f"
    with pytest.raises(download.ChecksumError):
        download.fetch("https://example.invalid/f", "0" * 64, dest, "f")
    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []

    ok = hashlib.sha256(payload).hexdigest()
    download.fetch("https://example.invalid/f", ok, dest, "f")
    assert dest.read_bytes() == payload
