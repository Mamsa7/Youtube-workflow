"""Tiny background-job runner so long generations don't block the UI.

Jobs persist progress into project["jobs"][step]; the frontend polls the
project endpoint and renders progress bars from that.
"""
from __future__ import annotations

import threading
import traceback
from typing import Callable

from . import store

_lock = threading.Lock()
JOBS: dict[tuple[str, str], dict] = {}


def is_running(pid: str, step: str | None = None) -> bool:
    with _lock:
        for (p, s), v in JOBS.items():
            if p == pid and (step is None or s == step) and v["thread"].is_alive():
                return True
    return False


def request_cancel(pid: str, step: str | None = None) -> None:
    with _lock:
        for (p, s), v in JOBS.items():
            if p == pid and (step is None or s == step):
                v["cancel"].set()


def start(pid: str, step: str, target: Callable, total: int = 0, message: str = "") -> None:
    """target(progress_cb, cancel_event). progress_cb(done=None, total=None, message=None)."""
    key = (pid, step)
    with _lock:
        existing = JOBS.get(key)
        if existing and existing["thread"].is_alive():
            raise RuntimeError(f"'{step}' is already running for this project")
        cancel = threading.Event()

        def runner():
            p = store.get_project(pid)
            if not p:
                return
            p["jobs"][step] = {"state": "running", "done": 0, "total": total, "message": message or "Working…"}
            store.save_project(p)

            def progress(done=None, tot=None, message=None):
                q = store.get_project(pid)
                if not q:
                    return
                st = q["jobs"].get(step, {"state": "running"})
                st["state"] = "running"
                if done is not None:
                    st["done"] = done
                if tot is not None:
                    st["total"] = tot
                if message is not None:
                    st["message"] = message
                q["jobs"][step] = st
                store.save_project(q)

            try:
                target(progress, cancel)
                q = store.get_project(pid)
                if q:
                    st = q["jobs"].get(step, {})
                    st["state"] = "cancelled" if cancel.is_set() else "done"
                    if cancel.is_set():
                        st["message"] = "Stopped by user"
                    q["jobs"][step] = st
                    store.save_project(q)
            except Exception as e:  # noqa: BLE001 - surfaced to the UI
                traceback.print_exc()
                q = store.get_project(pid)
                if q:
                    q["jobs"][step] = {"state": "error", "error": str(e)}
                    store.save_project(q)

        th = threading.Thread(target=runner, daemon=True, name=f"job-{pid}-{step}")
        JOBS[key] = {"thread": th, "cancel": cancel}
        th.start()
