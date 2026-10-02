"""StickStory Studio — API + static dashboard."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, jobs, store
from .services import (capcut_service, gemini_service, image_service,
                       sfx_service, tts_service, workflow)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="StickStory Studio", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ------------------------------------------------------------------ helpers
def _project_or_404(pid: str) -> dict:
    p = store.get_project(pid)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


def _safe_media(pid: str, rel: str) -> Path:
    base = store.project_dir(pid).resolve()
    p = (base / rel).resolve()
    if not str(p).startswith(str(base)) or not p.exists() or not p.is_file():
        raise HTTPException(404, "File not found")
    return p


def _ok(data=None, **extra):
    out = {"ok": True}
    if data is not None:
        out["data"] = data
    out.update(extra)
    return out


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return _ok(service="stickstory-studio")


# ------------------------------------------------------------------ settings
@app.get("/api/settings")
def get_settings():
    return _ok(config.get_settings())


@app.put("/api/settings")
def put_settings(payload: dict = Body(...)):
    return _ok(config.save_settings(payload))


@app.post("/api/settings/test")
def test_connection(payload: dict = Body(...)):
    service = payload.get("service")
    settings = config.get_settings()
    if settings.get("mock_mode") and service != "mock":
        return JSONResponse({"ok": False, "error": "Mock mode is ON — turn it off to test real APIs."},
                            status_code=400)
    try:
        if service == "gemini_text":
            msg = gemini_service.test_text(settings)
        elif service == "gemini_image":
            msg = gemini_service.test_image(settings)
        elif service == "tts":
            msg = gemini_service.test_tts(settings, config.DATA_DIR)
        else:
            raise HTTPException(400, f"Unknown service '{service}'")
        return _ok(message=msg)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)[:600]}, status_code=400)


@app.get("/api/voices")
def voices():
    return _ok(tts_service.list_edge_voices())


# ------------------------------------------------------------------ bible
@app.get("/api/bible")
def get_bible():
    return _ok(config.get_bible())


@app.put("/api/bible")
def put_bible(payload: dict = Body(...)):
    payload.pop("characters", None)        # managed via dedicated endpoints
    payload.pop("reference_images", None)
    current = config.get_bible()
    current.update({k: v for k, v in payload.items() if k in config.DEFAULT_BIBLE})
    return _ok(config.save_bible(current))


@app.post("/api/bible/characters")
def add_character(payload: dict = Body(...)):
    bible = config.get_bible()
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Character name required")
    ch = {"id": uuid.uuid4().hex[:8], "name": name,
          "description": (payload.get("description") or "").strip(), "sheet": None}
    bible["characters"].append(ch)
    config.save_bible(bible)
    return _ok(ch)


@app.put("/api/bible/characters/{cid}")
def edit_character(cid: str, payload: dict = Body(...)):
    bible = config.get_bible()
    for ch in bible["characters"]:
        if ch["id"] == cid:
            ch["name"] = (payload.get("name") or ch["name"]).strip()
            ch["description"] = (payload.get("description") or ch["description"]).strip()
            config.save_bible(bible)
            return _ok(ch)
    raise HTTPException(404, "Character not found")


@app.delete("/api/bible/characters/{cid}")
def delete_character(cid: str):
    bible = config.get_bible()
    before = len(bible["characters"])
    for ch in bible["characters"]:
        if ch["id"] == cid and ch.get("sheet"):
            (config.BIBLE_REFS_DIR / ch["sheet"]).unlink(missing_ok=True)
    bible["characters"] = [c for c in bible["characters"] if c["id"] != cid]
    if len(bible["characters"]) == before:
        raise HTTPException(404, "Character not found")
    config.save_bible(bible)
    return _ok()


@app.post("/api/bible/characters/{cid}/sheet")
async def upload_sheet(cid: str, file: UploadFile = File(...)):
    bible = config.get_bible()
    ch = next((c for c in bible["characters"] if c["id"] == cid), None)
    if not ch:
        raise HTTPException(404, "Character not found")
    ext = Path(file.filename or "sheet.png").suffix.lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(400, "Image files only")
    name = f"sheet_{cid}{ext}"
    (config.BIBLE_REFS_DIR / name).write_bytes(await file.read())
    ch["sheet"] = name
    config.save_bible(bible)
    return _ok(ch)


@app.post("/api/bible/refs")
async def upload_refs(files: list[UploadFile] = File(...)):
    bible = config.get_bible()
    saved = []
    for f in files:
        ext = Path(f.filename or "ref.png").suffix.lower() or ".png"
        if ext not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        name = f"ref_{uuid.uuid4().hex[:6]}{ext}"
        (config.BIBLE_REFS_DIR / name).write_bytes(await f.read())
        bible["reference_images"].append(name)
        saved.append(name)
    config.save_bible(bible)
    return _ok(saved= saved, count=len(saved))


@app.delete("/api/bible/refs/{filename}")
def delete_ref(filename: str):
    bible = config.get_bible()
    if filename in bible["reference_images"]:
        bible["reference_images"].remove(filename)
        (config.BIBLE_REFS_DIR / filename).unlink(missing_ok=True)
        config.save_bible(bible)
    return _ok()


@app.get("/api/bible/media/{filename}")
def bible_media(filename: str):
    p = (config.BIBLE_REFS_DIR / filename).resolve()
    if not str(p).startswith(str(config.BIBLE_REFS_DIR.resolve())) or not p.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(p)


# ------------------------------------------------------------------ sfx lib
@app.get("/api/sfx/library")
def sfx_library():
    return _ok(sfx_service.library())


@app.post("/api/sfx/library")
async def sfx_upload(file: UploadFile = File(...)):
    ext = Path(file.filename or "sfx.wav").suffix.lower()
    if ext not in (".wav", ".mp3", ".ogg", ".m4a"):
        raise HTTPException(400, "Audio files only (.wav/.mp3/.ogg/.m4a)")
    name = f"{Path(file.filename).stem[:40].strip() or 'sfx'}{ext}"
    dest = config.SFX_LIBRARY_DIR / name
    i = 1
    while dest.exists():
        dest = config.SFX_LIBRARY_DIR / f"{Path(file.filename).stem[:36]}_{i}{ext}"
        i += 1
    dest.write_bytes(await file.read())
    return _ok(name=dest.stem, file=dest.name)


@app.delete("/api/sfx/library/{filename}")
def sfx_delete(filename: str):
    (config.SFX_LIBRARY_DIR / filename).unlink(missing_ok=True)
    return _ok()


@app.get("/api/sfx/preview")
def sfx_preview(name: str | None = None, file: str | None = None, kind: str = "procedural"):
    if kind == "uploaded" and file:
        p = (config.SFX_LIBRARY_DIR / file).resolve()
        if not str(p).startswith(str(config.SFX_LIBRARY_DIR.resolve())) or not p.exists():
            raise HTTPException(404, "Not found")
        return FileResponse(p)
    name = name if name in sfx_service.REGISTRY else sfx_service.match_name(name or "whoosh")
    tmp = config.DATA_DIR / f"_preview_{name}.wav"
    sfx_service.synth(name, tmp)
    return FileResponse(tmp, media_type="audio/wav")


# ------------------------------------------------------------------ projects
@app.get("/api/projects")
def projects():
    return _ok(store.list_projects())


@app.post("/api/projects")
def create_project(payload: dict = Body(...)):
    if not (payload.get("source_transcript") or "").strip():
        payload["source_transcript"] = gemini_service.SAMPLE_TRANSCRIPT
    p = store.create_project(payload)
    if payload.get("autostart"):
        jobs.start(p["id"], "script", lambda prog, cancel: workflow.run_script(p["id"], prog, cancel),
                   total=1, message="Writing script…")
    return _ok(p)


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return _ok(_project_or_404(pid))


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    jobs.request_cancel(pid)
    store.delete_project(pid)
    return _ok()


@app.post("/api/projects/{pid}/jobs/cancel")
def cancel_jobs(pid: str, payload: dict = Body(default={})):
    jobs.request_cancel(pid, payload.get("step"))
    return _ok()


@app.put("/api/projects/{pid}")
def update_project_meta(pid: str, payload: dict = Body(...)):
    p = _project_or_404(pid)
    if payload.get("title"):
        p["title"] = payload["title"].strip()
    store.save_project(p)
    return _ok(p)


# ---- script
@app.post("/api/projects/{pid}/script/generate")
def gen_script(pid: str):
    _project_or_404(pid)
    if jobs.is_running(pid):
        raise HTTPException(409, "Another step is still running — wait or stop it first.")
    jobs.start(pid, "script", lambda prog, cancel: workflow.run_script(pid, prog, cancel),
               total=1, message="Writing script…")
    return _ok(started="script")


@app.put("/api/projects/{pid}/script")
def save_script(pid: str, payload: dict = Body(...)):
    p = _project_or_404(pid)
    script = payload.get("script")
    if not script or not script.get("beats"):
        raise HTTPException(400, "Script with beats required")
    p["script"] = script
    p["captions"] = None
    store.save_project(p)
    return _ok(p)


@app.post("/api/projects/{pid}/script/approve")
def approve_script(pid: str, payload: dict = Body(default={})):
    p = _project_or_404(pid)
    p["script_approved"] = bool(payload.get("approved", True))
    store.save_project(p)
    return _ok(approved=p["script_approved"])


@app.put("/api/projects/{pid}/beats/{bid}")
def edit_beat(pid: str, bid: str, payload: dict = Body(...)):
    p = _project_or_404(pid)
    beats = (p.get("script") or {}).get("beats") or []
    for b in beats:
        if b["id"] == bid:
            if "narration" in payload:
                b["narration"] = payload["narration"]
                p["captions"] = None
            if "visual" in payload:
                b["visual"] = payload["visual"]
            store.save_project(p)
            return _ok(b)
    raise HTTPException(404, "Beat not found")


@app.post("/api/projects/{pid}/beats/{bid}/visual_auto")
def auto_visual(pid: str, bid: str):
    p = _project_or_404(pid)
    settings = config.get_settings()
    beats = (p.get("script") or {}).get("beats") or []
    beat = next((b for b in beats if b["id"] == bid), None)
    if not beat:
        raise HTTPException(404, "Beat not found")
    if settings.get("mock_mode"):
        beat["visual"] = f"Storybook stick-character scene: {' '.join(beat['narration'].split()[:10])}…"
        store.save_project(p)
        return _ok(beat)
    try:
        beat["visual"] = gemini_service.improve_visual_prompt(
            beat["narration"], beat.get("visual", ""), config.get_bible(), settings)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)[:400]}, status_code=400)
    store.save_project(p)
    return _ok(beat)


# ---- voice
@app.post("/api/projects/{pid}/voice/generate")
def gen_voice(pid: str):
    p = _project_or_404(pid)
    if not p.get("script"):
        raise HTTPException(400, "Generate a script first.")
    if jobs.is_running(pid):
        raise HTTPException(409, "Another step is still running.")
    n = len(p["script"]["beats"])
    jobs.start(pid, "voice", lambda prog, cancel: workflow.run_voice(pid, prog, cancel),
               total=n, message="Synthesizing voiceover…")
    return _ok(started="voice")


# ---- images
@app.post("/api/projects/{pid}/images/generate")
def gen_images(pid: str, payload: dict = Body(default={})):
    p = _project_or_404(pid)
    if not p.get("script"):
        raise HTTPException(400, "Generate a script first.")
    if jobs.is_running(pid):
        raise HTTPException(409, "Another step is still running.")
    beat_ids = payload.get("beat_ids") or None
    total = len(beat_ids) if beat_ids else len(p["script"]["beats"])
    jobs.start(pid, "images",
               lambda prog, cancel: workflow.run_images(pid, prog, cancel, beat_ids=beat_ids),
               total=total, message="Generating scene images…")
    return _ok(started="images")


@app.post("/api/projects/{pid}/images/{bid}/regenerate")
def regen_image(pid: str, bid: str):
    p = _project_or_404(pid)
    if not p.get("script"):
        raise HTTPException(400, "Generate a script first.")
    if not any(b["id"] == bid for b in p["script"]["beats"]):
        raise HTTPException(404, "Beat not found")
    if jobs.is_running(pid, "images"):
        raise HTTPException(409, "Image generation already running.")
    jobs.start(pid, "images",
               lambda prog, cancel: workflow.run_images(pid, prog, cancel, beat_ids=[bid]),
               total=1, message=f"Repainting {bid}…")
    return _ok(started="images")


@app.post("/api/projects/{pid}/images/{bid}/upload")
async def upload_image(pid: str, bid: str, file: UploadFile = File(...)):
    import time
    p = _project_or_404(pid)
    if not any(b["id"] == bid for b in (p.get("script") or {}).get("beats", [])):
        raise HTTPException(404, "Beat not found")
    ext = Path(file.filename or "scene.png").suffix.lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(400, "Image files only")
    idx = bid[1:]
    name = f"beat_{idx}.png"
    (store.project_dir(pid) / "images" / name).write_bytes(await file.read())
    p = store.get_project(pid)
    p["images"][bid] = {"file": f"images/{name}", "status": "done", "approved": False,
                        "error": None, "prompt_used": "(uploaded by you)", "updated": time.time(),
                        "source": "upload"}
    store.save_project(p)
    return _ok(p["images"][bid])


@app.post("/api/projects/{pid}/images/{bid}/approve")
def approve_image(pid: str, bid: str, payload: dict = Body(default={})):
    p = _project_or_404(pid)
    rec = (p.get("images") or {}).get(bid)
    if rec is None:
        raise HTTPException(404, "No image for this beat yet")
    rec["approved"] = bool(payload.get("approved", True))
    store.save_project(p)
    return _ok(approved=rec["approved"])


# ---- sfx
@app.post("/api/projects/{pid}/sfx/plan")
def sfx_plan(pid: str):
    p = _project_or_404(pid)
    if not p.get("script"):
        raise HTTPException(400, "Generate a script first.")
    if jobs.is_running(pid):
        raise HTTPException(409, "Another step is still running.")
    jobs.start(pid, "sfx", lambda prog, cancel: workflow.run_sfx_plan(pid, prog, cancel),
               total=1, message="Planning sound effects…")
    return _ok(started="sfx")


@app.post("/api/projects/{pid}/sfx/cues")
def sfx_add_cue(pid: str, payload: dict = Body(...)):
    p = _project_or_404(pid)
    n_beats = len((p.get("script") or {}).get("beats") or []) or 1
    cue = {
        "beat": max(1, min(int(payload.get("beat", 1)), n_beats)),
        "offset": max(0.0, float(payload.get("offset", 0) or 0)),
        "name": str(payload.get("name", "whoosh"))[:40],
        "source": payload.get("source", "procedural"),
        "file": payload.get("file"),
        "description": str(payload.get("description", ""))[:120],
        "gain": float(payload.get("gain", 0.8) or 0.8),
    }
    p["sfx"].setdefault("plan", []).append(cue)
    p["sfx"]["mix"] = None
    store.save_project(p)
    return _ok(cue)


@app.put("/api/projects/{pid}/sfx/cues/{i}")
def sfx_edit_cue(pid: str, i: int, payload: dict = Body(...)):
    p = _project_or_404(pid)
    plan = p["sfx"].get("plan") or []
    if not (0 <= i < len(plan)):
        raise HTTPException(404, "Cue not found")
    cue = plan[i]
    for k in ("name", "source", "file", "description"):
        if k in payload:
            cue[k] = payload[k]
    for k in ("beat", "offset", "gain"):
        if k in payload:
            cue[k] = float(payload[k]) if k != "beat" else int(payload[k])
    cue.pop("time", None)
    cue.pop("resolved", None)
    p["sfx"]["mix"] = None
    store.save_project(p)
    return _ok(cue)


@app.delete("/api/projects/{pid}/sfx/cues/{i}")
def sfx_del_cue(pid: str, i: int):
    p = _project_or_404(pid)
    plan = p["sfx"].get("plan") or []
    if not (0 <= i < len(plan)):
        raise HTTPException(404, "Cue not found")
    plan.pop(i)
    p["sfx"]["mix"] = None
    store.save_project(p)
    return _ok()


@app.post("/api/projects/{pid}/sfx/build")
def sfx_build(pid: str):
    _project_or_404(pid)
    try:
        return _ok(workflow.build_sfx(pid))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)[:400]}, status_code=400)


# ---- captions & music
@app.post("/api/projects/{pid}/captions/generate")
def captions(pid: str):
    _project_or_404(pid)
    try:
        return _ok(workflow.build_captions(pid))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)[:400]}, status_code=400)


@app.post("/api/projects/{pid}/music")
async def music_upload(pid: str, file: UploadFile = File(...)):
    p = _project_or_404(pid)
    ext = Path(file.filename or "music.mp3").suffix.lower()
    if ext not in (".mp3", ".wav", ".ogg", ".m4a", ".aac"):
        raise HTTPException(400, "Audio files only")
    name = f"music{ext}"
    (store.project_dir(pid) / "uploads" / name).write_bytes(await file.read())
    p = store.get_project(pid)
    p["music"] = name
    store.save_project(p)
    return _ok(music=name)


# ---- export
@app.post("/api/projects/{pid}/export/package")
def export_package(pid: str):
    _project_or_404(pid)
    try:
        return _ok(capcut_service.build_package(pid))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)[:500]}, status_code=400)


@app.post("/api/projects/{pid}/export/draft")
def export_draft(pid: str):
    _project_or_404(pid)
    try:
        return _ok(capcut_service.build_draft(pid))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)[:500]}, status_code=400)


# ---- media
@app.get("/api/projects/{pid}/media/{rel:path}")
def project_media(pid: str, rel: str):
    p = _safe_media(pid, rel)
    return FileResponse(p, headers={"Cache-Control": "no-cache"})
