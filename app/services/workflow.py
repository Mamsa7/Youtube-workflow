"""Pipeline orchestration. The heavy steps run as background jobs; each one
persists progress so a quota error or a paused run never loses work.
"""
from __future__ import annotations

import time
from pathlib import Path

from .. import config, store
from . import gemini_service, image_service, sfx_service, timeline_service, tts_service


# ------------------------------------------------------------------- script
def run_script(pid: str, progress, cancel) -> None:
    settings = config.get_settings()
    bible = config.get_bible()
    p = store.get_project(pid)
    if not p:
        return
    transcript = (p.get("source_transcript") or "").strip() or gemini_service.SAMPLE_TRANSCRIPT
    opts = {
        "format": p["format"],
        "target_length_min": p["target_length_min"],
        "density": p["density"],
        "script_mode": p["script_mode"],
    }
    progress(0, 1, "Mock-writing script…" if settings.get("mock_mode")
             else f"Writing script with {settings.get('text_model')}…")
    if settings.get("mock_mode"):
        time.sleep(1.2)
        script = gemini_service.generate_script_mock(transcript, opts, bible)
    else:
        script = gemini_service.generate_script(transcript, opts, bible, settings)
    if cancel.is_set():
        return
    p = store.get_project(pid)
    p["script"] = script
    p["script_approved"] = False
    if script.get("title"):
        p["title"] = script["title"]
    # downstream artifacts are now stale
    p["voice"] = {"beats": [], "full": None, "provider": None, "voice_name": None}
    p["timeline"] = []
    p["images"] = {}
    p["sfx"] = {"plan": [], "mix": None, "files": {}}
    p["captions"] = None
    p["exports"] = {}
    store.save_project(p)
    progress(1, 1, f"Script ready — {len(script['beats'])} beats")


# ------------------------------------------------------------------- voice
def run_voice(pid: str, progress, cancel) -> None:
    settings = config.get_settings()
    p = store.get_project(pid)
    if not p or not p.get("script"):
        raise RuntimeError("Generate a script first.")
    beats = p["script"]["beats"]
    vdir = store.project_dir(pid) / "voice"
    for f in vdir.glob("beat_*"):
        f.unlink(missing_ok=True)
    (vdir / "voiceover_full.mp3").unlink(missing_ok=True)
    (vdir / "voiceover_full.wav").unlink(missing_ok=True)

    voice_beats: list[dict] = []
    for i, beat in enumerate(beats):
        if cancel.is_set():
            break
        progress(i, len(beats), f"Voicing scene {i + 1}/{len(beats)}…")
        info = tts_service.synth_beat(beat["narration"], vdir / f"beat_{i + 1:03d}", settings)
        f = vdir / info["file"]
        dur = tts_service.measure_duration(f)
        if dur <= 0:
            dur = max(0.8, len(beat["narration"].split()) * 0.38)
        voice_beats.append({
            "beat_id": beat["id"],
            "file": f"voice/{info['file']}",
            "duration": round(dur, 3),
            "provider": info["provider"],
        })
        if i % 5 == 4:  # checkpoint
            q = store.get_project(pid)
            q["voice"]["beats"] = voice_beats
            store.save_project(q)

    timeline = timeline_service.compute_timeline(beats, voice_beats)
    full_rel = None
    if not cancel.is_set() and voice_beats:
        ext = Path(voice_beats[0]["file"]).suffix
        full_path = vdir / f"voiceover_full{ext}"
        tts_service.concat_files([store.project_dir(pid) / vb["file"] for vb in voice_beats], full_path)
        full_rel = f"voice/{full_path.name}"

    q = store.get_project(pid)
    q["voice"] = {
        "beats": voice_beats,
        "full": full_rel,
        "provider": settings.get("tts_provider"),
        "voice_name": settings.get("edge_voice") if settings.get("tts_provider") == "edge-tts"
                      else settings.get("gtts_lang"),
    }
    q["timeline"] = timeline
    q["captions"] = None
    store.save_project(q)
    progress(len(beats), len(beats), "Voice ready — timeline locked to real audio timestamps")


# ------------------------------------------------------------------- images
def run_images(pid: str, progress, cancel, beat_ids: list | None = None) -> None:
    settings = config.get_settings()
    bible = config.get_bible()
    p = store.get_project(pid)
    if not p or not p.get("script"):
        raise RuntimeError("Generate a script first.")
    beats = p["script"]["beats"]
    idir = store.project_dir(pid) / "images"

    targets = [b for b in beats if beat_ids is None or b["id"] in set(beat_ids)]
    if beat_ids is None:  # resume mode: only what's missing
        def _done(b):
            rec = (p.get("images") or {}).get(b["id"]) or {}
            f = rec.get("file")
            return rec.get("status") == "done" and f and (store.project_dir(pid) / f).exists()
        targets = [b for b in targets if not _done(b)]
    if not targets:
        progress(1, 1, "All scene images already generated")
        return

    ref_paths = [] if settings.get("mock_mode") else image_service.bible_ref_paths(bible)
    total_beats = len(beats)
    consecutive_errors = 0

    for beat in targets:
        if cancel.is_set():
            break
        idx = int(beat["id"][1:])
        q = store.get_project(pid)
        rec = q["images"].setdefault(beat["id"], {})
        rec.update({"status": "generating", "error": None})
        store.save_project(q)
        done_count = sum(1 for v in (q.get("images") or {}).values() if v.get("status") == "done")
        progress(done_count, total_beats, f"Painting scene {idx}/{total_beats}…")
        try:
            prompt = image_service.compose_prompt(bible, beat["visual"], q["format"])
            data = image_service.generate_image(prompt, settings, fmt=q["format"], ref_paths=ref_paths)
            fname = f"beat_{idx:03d}.png"
            (idir / fname).write_bytes(data)
            q = store.get_project(pid)
            q["images"][beat["id"]] = {
                "file": f"images/{fname}", "status": "done", "approved": False,
                "error": None, "prompt_used": beat["visual"], "updated": time.time(),
                "source": "mock" if settings.get("mock_mode") else settings.get("image_backend"),
            }
            store.save_project(q)
            consecutive_errors = 0
            if not settings.get("mock_mode"):
                time.sleep(0.5)  # be gentle with rate limits
        except Exception as e:  # noqa: BLE001 - per-scene resilience
            q = store.get_project(pid)
            rec = q["images"].setdefault(beat["id"], {})
            rec.update({"status": "error", "error": str(e)[:500]})
            store.save_project(q)
            consecutive_errors += 1
            low = str(e).lower()
            if consecutive_errors >= 3 or any(k in low for k in ("quota", "429", "rate limit", "exhausted")):
                q = store.get_project(pid)
                q["jobs"]["images"] = {
                    "state": "paused",
                    "done": sum(1 for v in q["images"].values() if v.get("status") == "done"),
                    "total": total_beats,
                    "message": "Paused after API errors (likely rate limit). Progress saved — "
                               "hit Generate to resume, or switch provider in Settings.",
                    "error": str(e)[:300],
                }
                store.save_project(q)
                return
    done_count = sum(1 for v in (store.get_project(pid).get("images") or {}).values()
                     if v.get("status") == "done")
    progress(done_count, total_beats, f"Images complete — {done_count}/{total_beats}")


# ------------------------------------------------------------------- sfx
def run_sfx_plan(pid: str, progress, cancel) -> None:
    settings = config.get_settings()
    p = store.get_project(pid)
    if not p or not p.get("script"):
        raise RuntimeError("Generate a script first.")
    beats = p["script"]["beats"]
    timeline = p.get("timeline") or [
        {"start": i * 5.0, "end": (i + 1) * 5.0} for i in range(len(beats))]
    progress(0, 1, "Sound-designing with Gemini…" if not settings.get("mock_mode")
             else "Creating demo SFX plan…")
    if settings.get("mock_mode"):
        time.sleep(0.8)
        cues = gemini_service.generate_sfx_plan_mock(beats, timeline)
    else:
        cues = gemini_service.generate_sfx_plan(beats, timeline, settings)
    if cancel.is_set():
        return
    q = store.get_project(pid)
    q["sfx"]["plan"] = cues
    q["sfx"]["mix"] = None
    q["sfx"]["files"] = {}
    store.save_project(q)
    progress(1, 1, f"SFX plan ready — {len(cues)} cues")


def build_sfx(pid: str) -> dict:
    """Synthesize every cue and mix the SFX track. Synchronous (local, fast)."""
    p = store.get_project(pid)
    if not p:
        raise RuntimeError("Project not found")
    cues = (p.get("sfx") or {}).get("plan") or []
    if not cues:
        raise RuntimeError("No SFX cues yet — generate a plan or add cues manually.")
    timeline = p.get("timeline") or []
    beats = p.get("script", {}).get("beats", [])
    if not timeline:
        timeline = [{"start": i * 5.0, "end": (i + 1) * 5.0} for i in range(len(beats))]
    total = timeline[-1]["end"] if timeline else 60.0

    sdir = store.project_dir(pid) / "sfx"
    for f in sdir.glob("cue_*"):
        f.unlink(missing_ok=True)

    files: dict[str, str] = {}
    mix_cues = []
    lib = sfx_service.library()
    uploaded = {u["file"]: u for u in lib["uploaded"]}

    for i, cue in enumerate(cues):
        beat_idx = max(0, int(cue.get("beat", 1)) - 1)
        if beat_idx < len(timeline):
            base, end = timeline[beat_idx]["start"], timeline[beat_idx]["end"]
        else:
            base, end = beat_idx * 5.0, beat_idx * 5.0 + 5.0
        t = min(base + float(cue.get("offset", 0) or 0), max(base, end - 0.05))
        gain = float(cue.get("gain", 0.8) or 0.8)

        if cue.get("source") == "uploaded" and cue.get("file") in uploaded:
            src = config.SFX_LIBRARY_DIR / cue["file"]
            dst = sdir / f"cue_{i + 1:02d}_{src.name}"
            dst.write_bytes(src.read_bytes())
            cue["resolved"] = cue["file"]
        else:
            name = cue.get("name", "whoosh")
            if name not in sfx_service.REGISTRY:
                name = sfx_service.match_name(name)
            dst = sdir / f"cue_{i + 1:02d}_{name}.wav"
            sfx_service.synth(name, dst)
            cue["resolved"] = name
        cue["time"] = round(t, 3)
        files[str(i)] = f"sfx/{dst.name}"
        if dst.suffix.lower() == ".wav":
            mix_cues.append({"file": dst, "time": t, "gain": gain})

    mix_path = sdir / "sfx_mix.wav"
    sfx_service.mix(mix_cues, total, mix_path)

    q = store.get_project(pid)
    q["sfx"].update({"plan": cues, "mix": "sfx/sfx_mix.wav", "files": files})
    store.save_project(q)
    return {"cues": len(cues), "mix": "sfx/sfx_mix.wav", "duration": total}


def build_captions(pid: str) -> dict:
    p = store.get_project(pid)
    if not p or not p.get("timeline"):
        raise RuntimeError("Generate the voiceover first — captions use real audio timestamps.")
    srt = timeline_service.make_srt(p["timeline"])
    out = store.project_dir(pid) / "captions.srt"
    out.write_text(srt, encoding="utf-8")
    q = store.get_project(pid)
    q["captions"] = "captions.srt"
    store.save_project(q)
    return {"file": "captions.srt", "chars": len(srt)}
