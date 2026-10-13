"""Local app: FastAPI backend for the UI in app/ (built into src/videocut/web/dist/).

Security model (the app runs on the user's machine, next to their recordings):
- bound to 127.0.0.1 only; requests with another Host header are refused (DNS rebinding);
- a per-session token, printed and opened as `/?t=<token>`, is exchanged once per browser
  for an HttpOnly SameSite=Strict session cookie; every other request needs that cookie
  (or the token in an `X-Token` header, for scripts);
- writes (POST/PUT/PATCH/DELETE) from a browser must carry a same-origin Origin header;
- the human decisions (approve the plan, the personal-data decision, feedback) only come
  from these authenticated requests, never from an agent run (agent runs have no network);
- uploads stream to disk with a size cap, are checked by extension and ffprobe, and enter
  the job through `videocut tool job new` like a file given on the command line;
- file routes (stills, media) only serve known files inside the job's work/ and out/.

Runs: one pipeline run at a time (a queue, src/videocut/server/runs.py); the run's `say`
lines become events on the job's server-sent-event channel.
"""

import secrets
import webbrowser

from .app import create_app

__all__ = ["create_app", "serve"]


def serve(port: int = 4300, open_browser: bool = True) -> None:
    import uvicorn

    from .. import run

    run.install_stdout_router()  # run threads and request threads capture tool output
    token = secrets.token_urlsafe(24)
    app = create_app(token=token, port=port)
    url = f"http://127.0.0.1:{port}/?t={token}"
    print(f"Video Cut is running: {url}\nStop with Ctrl+C.", flush=True)
    if not app.state.ui_built:
        print("The app is not built yet: run ./start.sh (or `videocut setup`).", flush=True)
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        timeout_graceful_shutdown=2,
    )
