"""First start and settings: how Video Cut connects to Claude.

GET  /api/setup/status  the chosen mode, whether a key is stored, and what the official
                        `claude` program says about itself (asked in the background; the
                        answer is cached, `?refresh=1` asks again)
POST /api/setup/auth    {mode: local|apikey, api_key?}: choose the mode; a key goes to the
                        macOS keychain (like `videocut auth key`) and is never sent back
POST /api/setup/test    one tiny call through the official program ("Verbindung prüfen")

Only the official program is run; its configuration and credential files are never read.
"""

import re
import threading
import time
from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import claude_cli, settings

KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_\-]{20,300}$")
CACHE_S = 60


class AuthBody(BaseModel):
    mode: str = Field(pattern="^(local|apikey)$")
    api_key: str | None = Field(default=None, max_length=400)


class Detector:
    """Runs the status check in a thread so a page load never waits for the program."""

    def __init__(self, check: Callable[[], dict]):
        self.check = check
        self.lock = threading.Lock()
        self.result: dict | None = None
        self.at = 0.0
        self.thread: threading.Thread | None = None

    def _work(self) -> None:
        try:
            res = self.check()
        except Exception as e:  # noqa: BLE001 — shown as the detection's error
            res = {"installed": False, "signed_in": False, "error": str(e)}
        with self.lock:
            self.result, self.at, self.thread = res, time.monotonic(), None

    def get(self, refresh: bool = False) -> dict:
        with self.lock:
            stale = self.result is None or refresh or time.monotonic() - self.at > CACHE_S
            if stale and self.thread is None:
                self.thread = threading.Thread(target=self._work, daemon=True)
                self.thread.start()
            checking = self.thread is not None
            res = dict(self.result or {})
        res.pop("path", None)  # where the program lives is not the page's business
        return {"state": "checking" if checking else "done", **res}

    def wait(self, timeout: float = 30) -> None:
        t = self.thread
        if t is not None:
            t.join(timeout)


def _key() -> str | None:
    try:
        return settings.api_key()
    except Exception:  # noqa: BLE001 — no keychain (CI, Linux): no key
        return None


def router(
    check: Callable[[], dict] = claude_cli.status,
    tester: Callable[[str | None], dict] = claude_cli.test_call,
) -> APIRouter:
    r = APIRouter(prefix="/api/setup")
    detector = Detector(check)
    r.detector = detector  # type: ignore[attr-defined] — tests wait on it

    def view(refresh: bool = False) -> dict:
        conf = settings.load()
        return {
            "done": settings.setup_done(),
            "mode": conf.get("auth"),
            "api_key_stored": _key() is not None,
            "claude": detector.get(refresh),
            "console_url": claude_cli.CONSOLE_URL,
        }

    @r.get("/status")
    def status(refresh: bool = False) -> dict:
        return view(refresh)

    @r.post("/auth")
    def auth(body: AuthBody) -> dict:
        if body.mode == "apikey":
            key = (body.api_key or "").strip()
            if key:
                if not KEY_RE.match(key):
                    raise HTTPException(
                        422, {"message": "That does not look like an Anthropic API key.",
                              "code": "bad_key"},
                    )  # fmt: skip
                try:
                    settings.set_api_key(key)
                except Exception:  # noqa: BLE001 — the keychain refused
                    raise HTTPException(
                        500, {"message": "The key could not be stored in the keychain.",
                              "code": "keychain"},
                    ) from None  # fmt: skip
            elif _key() is None:
                raise HTTPException(422, {"message": "No API key given.", "code": "no_key"})
        settings.save(auth=body.mode, setup_done=True)
        return view()

    @r.post("/test")
    def test() -> dict:
        mode = settings.load().get("auth")
        key = _key() if mode == "apikey" else None
        if mode == "apikey" and not key:
            return {"ok": False, "message": "no API key stored", "seconds": 0}
        res = tester(key)
        return {
            "ok": bool(res.get("ok")),
            "message": str(res.get("message") or "")[:300],
            "seconds": res.get("seconds"),
            "mode": mode,
        }

    return r
