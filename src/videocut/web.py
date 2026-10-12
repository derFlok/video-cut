"""Local web app. Bound to 127.0.0.1 only; every API call needs the per-session token.

Current scope: connection check (a button starts a Claude run, progress streams live).
The full app (video types, uploads, brands, approval, preview) builds on these pieces.
"""

import json
import secrets
import threading
import time
import webbrowser
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, urlparse

from . import agent
from .paths import JOBS

PING_PROMPT = "Run the Video Cut ping."


class Job:
    def __init__(self, job_id: str):
        self.id = job_id
        self.events: list[dict] = []
        self.done = False
        self.cond = threading.Condition()

    def push(self, ev: dict) -> None:
        with self.cond:
            self.events.append(ev)
            self.cond.notify_all()

    def finish(self) -> None:
        with self.cond:
            self.done = True
            self.cond.notify_all()


class App:
    def __init__(self, port: int):
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.jobs: dict[str, Job] = {}
        self.busy = threading.Lock()  # one Claude run at a time

    def start_ping(self) -> Job | None:
        if not self.busy.acquire(blocking=False):
            return None
        job_id = time.strftime("ping-%Y%m%d-%H%M%S")
        job = Job(job_id)
        self.jobs[job_id] = job
        folder = JOBS / job_id
        folder.mkdir(parents=True, exist_ok=True)

        def work() -> None:
            t0 = time.monotonic()
            try:
                res = agent.run(
                    PING_PROMPT, folder, lambda e: job.push(asdict(e)), log=folder / "claude.jsonl"
                )
                pong = (folder / "pong.txt").exists()
                job.push(
                    {
                        "kind": "summary",
                        "text": "",
                        "data": {
                            "ok": res.ok and pong,
                            "pong_file": pong,
                            "auth": res.auth,
                            "cost_usd": res.cost_usd,
                            "turns": res.turns,
                            "seconds": round(time.monotonic() - t0, 1),
                        },
                    }
                )
            except Exception as e:  # noqa: BLE001 — surface every failure in the UI
                job.push({"kind": "error", "text": str(e), "data": {}})
            finally:
                job.finish()
                self.busy.release()

        threading.Thread(target=work, daemon=True).start()
        return job


def handler(app: App) -> type[BaseHTTPRequestHandler]:
    allowed_hosts = {f"127.0.0.1:{app.port}", f"localhost:{app.port}"}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _authorised(self) -> bool:
            if self.headers.get("Host") not in allowed_hosts:  # DNS-rebinding guard
                return False
            q = parse_qs(urlparse(self.path).query)
            token = self.headers.get("X-Token") or q.get("t", [""])[0]
            return secrets.compare_digest(token, app.token)

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, data: dict) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/favicon.ico":
                return self._send(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
            if not self._authorised():
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            if path == "/":
                page = resources.files("videocut").joinpath("web/index.html").read_bytes()
                return self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            if path.startswith("/api/jobs/") and path.endswith("/events"):
                job = app.jobs.get(path.split("/")[3])
                if not job:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "unknown job"})
                return self._stream(job)
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):
            if not self._authorised():
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            if urlparse(self.path).path == "/api/ping":
                job = app.start_ping()
                if not job:
                    return self._json(HTTPStatus.CONFLICT, {"error": "a run is already active"})
                return self._json(HTTPStatus.ACCEPTED, {"job": job.id})
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def _stream(self, job: Job) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            i = 0
            while True:
                with job.cond:
                    if i >= len(job.events) and not job.done:
                        job.cond.wait(timeout=15)
                    batch = job.events[i:]
                    finished = job.done
                try:
                    for ev in batch:
                        self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                    i += len(batch)
                    if finished and i >= len(job.events):
                        self.wfile.write(b"event: end\ndata: {}\n\n")
                        self.wfile.flush()
                        return
                    if not batch:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return

    return H


def serve(port: int = 4300, open_browser: bool = True) -> None:
    app = App(port)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(app))
    url = f"http://127.0.0.1:{port}/?t={app.token}"
    print(f"Video Cut is running: {url}\nStop with Ctrl+C.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
