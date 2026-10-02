"""Project persistence. Each project is a folder under data/projects/<id>/."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from .config import PROJECTS_DIR

_lock = threading.Lock()


def _pdir(pid: str) -> Path:
    return PROJECTS_DIR / pid


def _pfile(pid: str) -> Path:
    return _pdir(pid) / "project.json"


def project_dir(pid: str) -> Path:
    return _pdir(pid)


def create_project(data: dict) -> dict:
    pid = uuid.uuid4().hex[:10]
    d = _pdir(pid)
    for sub in ("images", "voice", "sfx", "exports", "uploads"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    now = time.time()
    proj = {
        "id": pid,
        "created_at": now,
        "updated_at": now,
        "title": (data.get("title") or "").strip() or "Untitled Project",
        "source_transcript": data.get("source_transcript", ""),
        "format": data.get("format", "16:9"),
        "target_length_min": float(data.get("target_length_min", 8) or 8),
        "density": data.get("density", "normal"),
        "script_mode": data.get("script_mode", "original"),
        "script": None,                 # {"title","description","tags":[],"beats":[{"id","narration","visual"}]}
        "script_approved": False,
        "voice": {"beats": [], "full": None, "provider": None, "voice_name": None},
        "timeline": [],                 # [{"beat_id","index","start","end","text","movement"}]
        "images": {},                   # beat_id -> {"file","status","approved","error","prompt_used","updated"}
        "sfx": {"plan": [], "mix": None, "files": {}},
        "captions": None,               # relative path of srt
        "music": None,                  # filename inside project uploads
        "exports": {},                  # {"package": {...}, "draft": {...}}
        "jobs": {},                     # step -> {"state","done","total","message","error"}
    }
    save_project(proj)
    return proj


def get_project(pid: str) -> dict | None:
    f = _pfile(pid)
    if not f.exists():
        return None
    try:
        with open(f, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def save_project(proj: dict) -> None:
    proj["updated_at"] = time.time()
    f = _pfile(proj["id"])
    tmp = f.with_suffix(".tmp")
    with _lock:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(proj, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, f)


def list_projects() -> list[dict]:
    out = []
    for d in PROJECTS_DIR.iterdir():
        if not d.is_dir():
            continue
        p = get_project(d.name)
        if not p:
            continue
        beats = (p.get("script") or {}).get("beats") or []
        imgs = p.get("images") or {}
        done = sum(1 for v in imgs.values() if v.get("status") == "done")
        out.append({
            "id": p["id"],
            "title": p["title"],
            "created_at": p["created_at"],
            "updated_at": p["updated_at"],
            "format": p["format"],
            "script_done": bool(p.get("script")),
            "voice_done": bool((p.get("voice") or {}).get("full")),
            "beats": len(beats),
            "images_done": done,
            "jobs": p.get("jobs", {}),
        })
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    return out


def delete_project(pid: str) -> bool:
    d = _pdir(pid)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False
