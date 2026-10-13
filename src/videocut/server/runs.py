"""One pipeline run at a time: a queue, a worker thread, and an event channel per job.

A run is a function `fn(say, ask) -> exit code` (src/videocut/run.py's `run(...)` with
`say`/`ask` bound to the channel). Its `say` lines become events:
    {"t": "stage", "stage": ...}   a line that starts a pipeline stage ("prep:", "cut: ...");
                                   stage ids: prep, plan, approval, cut, overlays, verify,
                                   done, revise (a feedback run), plan_revise (a plan
                                   feedback run), error
    {"t": "tool", "text": ...}     a Claude tool call ("  · ...")
    {"t": "log", "text": ...}      everything else
and its end:
    {"t": "gate", "gate": "plan"}    exit code 3: the plan waits for the human (/approve);
                                     stage "approval"
    {"t": "gate", "gate": "result"}  exit code 0 or 4: the reel is there (/feedback);
                                     stage "done"
    {"t": "error", "text": ...}      a failure, with the reason; stage "error"
    {"t": "done", "ok": bool}
`ask` never blocks: the plan gate and the personal-data question are answered by the UI
(/approve), so a run that would ask ends at the gate instead.
"""

import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .. import agent
from .. import run as pipeline
from ..tools.common import ToolError

STAGE_RE = re.compile(r"\n?(prep|plan|cut|overlays|revise|verify)\b")
# What a run.py stage line means in a run of each kind: a plan feedback run re-plans
# (plan_revise), a feedback run revises the cut (revise) and then verifies.
STAGE_BY_KIND = {
    "plan_feedback": {"plan": "plan_revise"},
    "feedback": {"plan": "revise", "cut": "revise", "overlays": "revise"},
}
GATE_CODES = {3: "plan", 0: "result", 4: "result"}
MAX_EVENTS = 5000  # per run; later log lines are dropped (stage/gate/done are kept)
NOT_APPROVED = "Not approved"  # run.not_approved(): CLI hints, replaced by the gate event

RunFn = Callable[[Callable[[str], None], Callable[[str], str]], int]


class Busy(Exception):
    """The job already has a run queued or active."""


@dataclass
class Channel:
    """Events of the job's current (or last) run. `seq` counts every event ever pushed,
    so a reader can follow with an index across runs."""

    job: str
    events: list[dict] = field(default_factory=list)
    base: int = 0  # seq of events[0]
    state: str = "idle"  # idle | queued | running
    kind: str | None = None  # what the current or last run does (start, approve, ...)
    stage: str | None = None
    gate: str | None = None
    ok: bool | None = None
    error: str | None = None
    failed_stage: str | None = None  # the stage a failed run was in
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def seq(self) -> int:
        return self.base + len(self.events)

    def reset(self, kind: str) -> None:
        with self.lock:
            self.base += len(self.events)
            self.events = []
            self.state, self.kind = "queued", kind
            self.stage = self.gate = self.ok = self.error = self.failed_stage = None

    def push(self, ev: dict) -> None:
        with self.lock:
            if ev["t"] == "stage":
                self.stage = ev["stage"]
            elif ev["t"] == "gate":
                self.gate = ev["gate"]
            elif ev["t"] == "error":
                self.error = ev["text"]
            elif ev["t"] == "done":
                self.ok = ev["ok"]
            if len(self.events) >= MAX_EVENTS and ev["t"] in ("log", "tool"):
                return  # a runaway log; the run's stage/gate/error/done events still come
            self.events.append(ev)

    def fail(self, text: str) -> None:
        self.failed_stage = self.stage
        self.push({"t": "stage", "stage": "error"})
        self.push({"t": "error", "text": text})

    def since(self, seq: int) -> tuple[list[dict], int]:
        """Events from `seq` on (from the start of the current run when `seq` is older)."""
        with self.lock:
            i = max(seq - self.base, 0)
            return self.events[i:], self.seq

    def say(self, line: str) -> None:
        text = line.rstrip()
        if not text.strip():
            return
        if text.startswith("  · "):
            self.push({"t": "tool", "text": text[4:]})
            return
        if text.startswith(NOT_APPROVED):
            return
        m = STAGE_RE.match(text)
        if m:
            stage = STAGE_BY_KIND.get(self.kind or "", {}).get(m.group(1), m.group(1))
            if stage != self.stage:
                self.push({"t": "stage", "stage": stage})
        self.push({"t": "log", "text": text.lstrip("\n")})


def no_ask(prompt: str) -> str:
    """run.py asks at the plan gate; the UI answers via /approve, so the run stops there.
    (Any other question gets the safe answer: an empty one, i.e. redact everything.)"""
    return ""


class Runs:
    def __init__(self):
        self.channels: dict[str, Channel] = {}
        self.queue: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        # Held by a request across "no run active" check -> file change -> submit, so an
        # approval or a plan edit never lands next to a run another request just queued.
        self.decide = threading.RLock()
        self.worker: threading.Thread | None = None

    def channel(self, job_id: str) -> Channel:
        with self.lock:
            return self.channels.setdefault(job_id, Channel(job_id))

    def busy(self, job_id: str) -> bool:
        ch = self.channels.get(job_id)
        return ch is not None and ch.state != "idle"

    def active(self) -> str | None:
        """The job whose run is executing now."""
        return next((j for j, c in self.channels.items() if c.state == "running"), None)

    def submit(self, job_id: str, kind: str, fn: RunFn) -> Channel:
        ch = self.channel(job_id)
        with self.lock:
            if ch.state != "idle":
                raise Busy(job_id)
            ch.reset(kind)
            self.queue.put((ch, fn))
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self._work, name="videocut-run", daemon=True)
                self.worker.start()
        return ch

    def _work(self) -> None:
        while True:
            ch, fn = self.queue.get()
            ch.state = "running"
            t0 = time.monotonic()
            ok = False
            try:
                code = fn(ch.say, no_ask)
                gate = GATE_CODES.get(code)
                if gate:
                    ch.push({"t": "stage", "stage": "approval" if gate == "plan" else "done"})
                    ch.push({"t": "gate", "gate": gate})
                else:
                    ch.fail(f"the run ended with exit code {code}")
                ok = gate is not None
            except (pipeline.RunError, agent.AgentError, ToolError, NotImplementedError) as e:
                ch.fail(str(e))
            except BaseException as e:  # noqa: BLE001 — every failure must reach the UI,
                ch.fail(f"{type(e).__name__}: {e}")  # and the worker must survive it
            finally:
                # idle before "done", so a client that reloads the job on "done" sees the
                # end state; under self.lock, so no new run's reset() lands in between
                with self.lock:
                    ch.state = "idle"
                    ch.push({"t": "done", "ok": ok, "wall_s": round(time.monotonic() - t0, 1)})
                self.queue.task_done()

    def wait_idle(self, timeout: float = 10.0) -> None:
        """Tests: block until every queued run has finished."""
        end = time.monotonic() + timeout
        while any(c.state != "idle" for c in list(self.channels.values())):
            if time.monotonic() > end:
                raise TimeoutError("runs did not finish")
            time.sleep(0.01)
