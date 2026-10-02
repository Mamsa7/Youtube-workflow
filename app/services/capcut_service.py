"""CapCut export.

Two modes:

1. **CapCut Ready Package** (recommended, future-proof): a zip with ordered
   images, voiceover, SFX (+ pre-mixed SFX track), SRT captions and a
   timeline manifest with every scene's start/end and camera movement.
   Import takes ~2 minutes and survives any CapCut update.

2. **CapCut Draft** (experimental): writes a draft folder in CapCut's
   desktop draft format directly. CapCut's internal JSON is undocumented and
   can change between versions — when it works it's near-one-click.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
import zipfile  # noqa: F401 (used by shutil.make_archive under the hood)
from pathlib import Path

from .. import store
from . import timeline_service


def _us(sec: float) -> int:
    return int(round(float(sec) * 1_000_000))


def _uid() -> str:
    return uuid.uuid4().hex.upper()


def _slug(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in (name or "project"))
    return "-".join(keep.split())[:40] or "project"


def _check_ready(p: dict, pid: str) -> list[dict]:
    tl = p.get("timeline") or []
    if not tl:
        raise RuntimeError("Generate the voiceover first — the timeline comes from real audio.")
    missing = []
    for e in tl:
        rec = (p.get("images") or {}).get(e["beat_id"]) or {}
        f = rec.get("file")
        if rec.get("status") != "done" or not f or not (store.project_dir(pid) / f).exists():
            missing.append(e["index"] + 1)
    if missing:
        raise RuntimeError(
            f"{len(missing)} scene image(s) are missing (first: scene {missing[0]}). "
            "Generate or upload every scene image first.")
    if not (p.get("voice") or {}).get("full"):
        raise RuntimeError("Full voiceover not found — run the Voice step.")
    return tl


# ------------------------------------------------------------ package mode
def build_package(pid: str) -> dict:
    p = store.get_project(pid)
    if not p:
        raise RuntimeError("Project not found")
    tl = _check_ready(p, pid)
    pdir = store.project_dir(pid)
    exp = pdir / "exports" / "capcut_package"
    if exp.exists():
        shutil.rmtree(exp)
    for sub in ("images", "voice", "sfx"):
        (exp / sub).mkdir(parents=True, exist_ok=True)

    images_map = p.get("images") or {}
    scenes = []
    for e in tl:
        rec = images_map[e["beat_id"]]
        src = pdir / rec["file"]
        dst = exp / "images" / f"{e['index'] + 1:03d}_scene.png"
        shutil.copyfile(src, dst)
        scenes.append({
            "scene": e["index"] + 1,
            "image": f"images/{dst.name}",
            "start": e["start"], "end": e["end"], "duration": e["duration"],
            "movement": e["movement"],
            "movement_params": timeline_service.manifest_movement(e["movement"]),
            "voiceover_text": e["text"],
        })

    voice_rel = p["voice"]["full"]
    voice_dst = exp / "voice" / Path(voice_rel).name
    shutil.copyfile(pdir / voice_rel, voice_dst)
    for vb in p["voice"].get("beats", []):
        src = pdir / vb["file"]
        if src.exists():
            shutil.copyfile(src, exp / "voice" / src.name)

    sfx_cues = []
    for i, rel in (p["sfx"].get("files") or {}).items():
        src = pdir / rel
        if src.exists():
            shutil.copyfile(src, exp / "sfx" / src.name)
    for cue in (p["sfx"].get("plan") or []):
        if "time" in cue:
            sfx_cues.append({
                "time": cue["time"], "sound": cue.get("resolved") or cue.get("name"),
                "gain": cue.get("gain", 0.8), "description": cue.get("description", ""),
                "scene": cue.get("beat"),
            })
    if p["sfx"].get("mix") and (pdir / p["sfx"]["mix"]).exists():
        shutil.copyfile(pdir / p["sfx"]["mix"], exp / "sfx" / "sfx_mix.wav")

    srt = timeline_service.make_srt(tl)
    (exp / "captions.srt").write_text(srt, encoding="utf-8")
    (pdir / "captions.srt").write_text(srt, encoding="utf-8")

    music_rel = None
    if p.get("music"):
        src = pdir / "uploads" / p["music"]
        if src.exists():
            music_rel = src.name
            shutil.copyfile(src, exp / src.name)

    total = tl[-1]["end"]
    manifest = {
        "title": p["title"],
        "format": p["format"],
        "canvas": {"width": 1080, "height": 1920} if p["format"] == "9:16"
                  else {"width": 1920, "height": 1080},
        "duration_seconds": round(total, 3),
        "scenes": scenes,
        "voiceover_full": f"voice/{voice_dst.name}",
        "sfx_cues": sorted(sfx_cues, key=lambda c: c["time"]),
        "sfx_mix": "sfx/sfx_mix.wav" if p["sfx"].get("mix") else None,
        "captions": "captions.srt",
        "music": music_rel,
        "how_to": "See README_IMPORT.txt — everything is timed; just follow the numbers.",
    }
    (exp / "timeline_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    readme = f"""CAPCUT READY PACKAGE — {p['title']}
{'=' * 60}
Total runtime: {int(total // 60)}:{total % 60:04.1f}  ({len(scenes)} scenes)

FASTEST WORKFLOW (~2 minutes):
1. New CapCut project ({p['format']}).
2. Drag ALL files from /images onto the timeline in filename order — they're
   already numbered. CapCut stacks them back-to-back.
3. Select every image clip, set duration per timeline_manifest.json
   (each scene's exact seconds are listed there and below).
4. Drag voice/{voice_dst.name} onto an audio track at 00:00.
5. Drag sfx/sfx_mix.wav onto ANOTHER audio track at 00:00 — every effect is
   already pre-placed at the right timestamp. (Lower its volume to taste.)
6. Text → Import captions → captions.srt (or use Auto-captions).
{"7. Drop " + music_rel + " on a music track, volume ~10-15%." if music_rel else "7. Optional: add background music at low volume."}
8. Export.

SCENE TIMINGS (image durations, seconds):
""" + "\n".join(
        f"  {s['scene']:>3}. {s['start']:>7.2f} → {s['end']:>7.2f}  ({s['duration']:.2f}s) "
        f"{s['movement']:<11} {s['image']}" for s in scenes) + """

Camera movement suggestions are in the manifest (movement_params). Apply a
slow zoom/pan keyframe per image so statics never feel static.
"""
    (exp / "README_IMPORT.txt").write_text(readme, encoding="utf-8")

    zip_base = pdir / "exports" / f"capcut_package_{_slug(p['title'])}"
    zip_path = shutil.make_archive(str(zip_base), "zip", root_dir=exp)
    record = {"kind": "package", "file": f"exports/{Path(zip_path).name}",
              "created": time.time(), "scenes": len(scenes)}
    q = store.get_project(pid)
    q["exports"]["package"] = record
    q["captions"] = "captions.srt"
    store.save_project(q)
    return record


# -------------------------------------------------------------- draft mode
def build_draft(pid: str) -> dict:
    p = store.get_project(pid)
    if not p:
        raise RuntimeError("Project not found")
    tl = _check_ready(p, pid)
    pdir = store.project_dir(pid)
    name = f"CapCutDraft_{_slug(p['title'])}"
    root = pdir / "exports" / "drafts"
    if root.exists():
        shutil.rmtree(root)
    folder = root / name
    assets = folder / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    W, H = (1080, 1920) if p["format"] == "9:16" else (1920, 1080)
    images_map = p.get("images") or {}

    # copy assets with stable names
    img_paths = {}
    for e in tl:
        src = pdir / images_map[e["beat_id"]]["file"]
        dst = assets / f"scene_{e['index'] + 1:03d}.png"
        shutil.copyfile(src, dst)
        img_paths[e["index"]] = dst
    voice_src = pdir / p["voice"]["full"]
    voice_dst = assets / ("voiceover" + voice_src.suffix)
    shutil.copyfile(voice_src, voice_dst)
    sfx_dst = None
    if p["sfx"].get("mix") and (pdir / p["sfx"]["mix"]).exists():
        sfx_dst = assets / "sfx_mix.wav"
        shutil.copyfile(pdir / p["sfx"]["mix"], sfx_dst)
    music_dst = None
    if p.get("music") and (pdir / "uploads" / p["music"]).exists():
        music_dst = assets / p["music"]
        shutil.copyfile(pdir / "uploads" / p["music"], music_dst)

    total_us = _us(tl[-1]["end"])
    now_ms = int(time.time() * 1000)

    def video_material(path: Path, dur_us: int) -> dict:
        return {
            "id": _uid(), "type": "photo", "material_name": path.name,
            "path": str(path.resolve()), "duration": dur_us, "width": W, "height": H,
            "has_audio": False, "category_id": "", "category_name": "local", "check_flag": 63487,
            "crop": {"lower_left_x": 0.0, "lower_left_y": 1.0, "lower_right_x": 1.0,
                     "lower_right_y": 1.0, "upper_left_x": 0.0, "upper_left_y": 0.0,
                     "upper_right_x": 1.0, "upper_right_y": 0.0},
            "crop_ratio": "free", "crop_scale": 1.0, "extra_type_option": 0,
            "formula_id": "", "freeze": None, "gameplay": None, "intensifies_audio_id": "",
            "intensifies_path": "", "is_ai_generate_content": True, "is_copyright": False,
            "is_text_edit_overdub": False, "is_unified_beauty_mode": False, "local_id": "",
            "local_material_id": "", "material_id": "", "material_url": "",
            "matting": {"flag": 0, "has_use_quick_brush": False, "has_use_quick_eraser": False,
                        "interactiveTime": [], "path": "", "reverse": False,
                        "strokes": [], "thumbnail_path": ""},
            "media_path": "", "object_locked": None, "origin_material_id": "",
            "picture_from": "none", "picture_set_category_id": "", "picture_set_category_name": "",
            "request_id": "", "reverse_intensifies_path": "", "reverse_path": "", "source": 0,
            "source_platform": 0, "stable": None, "team_id": "",
            "video_algorithm": {"algorithms": [], "deflicker": None, "motion_blur_config": None,
                                "noise_reduction": None, "path": "", "quality_enhance": None,
                                "time_range": None},
            "audio_fade": None, "cartoon_id": "", "mid": "", "local_material_path": "",
        }

    def segment(material_id: str, start_us: int, dur_us: int, render_index: int = 0) -> dict:
        return {
            "id": _uid(), "material_id": material_id,
            "target_timerange": {"duration": dur_us, "start": start_us},
            "source_timerange": {"duration": dur_us, "start": 0},
            "cartoon": False,
            "clip": {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False},
                     "rotation": 0.0, "scale": {"x": 1.0, "y": 1.0},
                     "transform": {"x": 0.0, "y": 0.0}},
            "common_keyframes": [], "enable_adjust": True, "enable_color_curves": True,
            "enable_color_match_adjust": False, "enable_color_wheels": True,
            "enable_lut": True, "enable_smart_color_adjust": False,
            "extra_material_refs": [], "group_id": "",
            "hdr_settings": {"intensity": 1.0, "mode": "", "nits": 1000},
            "intensifies_audio_path": "", "intensifies_path": "",
            "is_ai_generate_content": False, "is_placeholder": False, "is_tone_modify": False,
            "keyframe_refs": [], "last_nonzero_volume": 1.0, "render_index": render_index,
            "responsive_layout": {"enable": False, "horizontal_pos": 0, "size_pos": 0,
                                  "target_follow": "", "vertical_pos": 0},
            "reverse": False, "speed": 1.0, "template_id": "", "template_scene": "default",
            "track_attribute": 0, "track_render_index": 0,
            "uniform_scale": {"on": True, "value": 1.0}, "visible": True, "volume": 1.0,
        }

    def audio_material(path: Path, dur_us: int, name: str) -> dict:
        return {
            "id": _uid(), "type": "extract_music", "name": name,
            "path": str(path.resolve()), "duration": dur_us,
            "app_id": 0, "category_id": "", "category_name": "local", "check_flag": 1,
            "copyright_limit_type": "none", "effect_id": "", "formula_id": "",
            "intensifies_path": "", "is_ai_clone_tone": False, "is_text_edit_overdub": False,
            "is_ugc": False, "local_material_id": "", "music_id": "", "query": "",
            "request_id": "", "resource_id": "", "search_id": "", "source_platform": 0,
            "team_id": "", "text_id": "", "tone_category_id": "", "tone_category_name": "",
            "tone_effect_id": "", "tone_effect_name": "", "tone_speaker": "", "tone_type": "",
            "video_id": "", "wave_points": [], "audio_fade": None, "lyric_path": "",
        }

    def text_material(content: str) -> dict:
        return {
            "id": _uid(), "type": "text", "content": content,
            "add_type": 0, "alignment": 1, "background_alpha": 1.0, "background_color": "",
            "background_height": 0.14, "background_horizontal_offset": 0.0,
            "background_round_radius": 0.0, "background_style": 0, "background_vertical_offset": 0.0,
            "background_width": 0.14, "bold_width": 0.0, "border_alpha": 1.0,
            "border_color": "", "border_width": 0.08, "calculate_type": 0, "caption_template_info": None,
            "check_flag": 7, "combo_info": None, "default_keywords": None, "degree": 0,
            "display_height": 0, "display_width": 0, "drop_shadow_alpha": 1.0,
            "drop_shadow_angle": 0.0, "drop_shadow_color": "", "drop_shadow_distance": 0.0,
            "drop_shadow_point": {"x": 0.0, "y": 0.0}, "emotion_size": 0.0,
            "error_color": "", "fill_color": "#ffffff", "fixed_height": -1.0, "fixed_width": -1.0,
            "font_category_id": "", "font_category_name": "", "font_id": "", "font_name": "",
            "font_path": "", "font_resource_id": "", "font_size": 9.0, "font_title": "none",
            "font_url": "", "fonts": [], "force_apply_line_max_width": False,
            "global_alpha": 1.0, "group_id": "", "has_shadow": False, "initial_scale": 1.0,
            "intent": {}, "is_rich_text": False, "italic_degree": 0, "ktv_color": "",
            "layer_weight": 1, "layers": [], "letter_spacing": 0.0, "line_feed": 1,
            "line_max_width": 0.86, "line_spacing": 0.02, "multi_language_current": "none",
            "name": "", "operator": 0, "original_size": [], "preset_category": "",
            "preset_category_id": "", "preset_has_set_alignment": False, "preset_id": "",
            "preset_index": 0, "recognize_task_id": "", "recognize_type": 0, "relevance_segment": [],
            "source": "selfbuilt", "speech_to_text_id": "", "style_name": "", "styles": [],
            "sub_type": 0, "text_alpha": 1.0, "text_intro": "", "text_outro": "",
            "text_shape": "", "text_size": 30.0, "text_to_audio_ids": [], "tts_auto_update": False,
            "typesetting": 0, "underline": False, "underline_offset": 0.22,
            "underline_width": 0.05, "use_effect_default_color": True, "words": [],
            "is_white_list_follow_lyric": False, "font_team_id": "", "font_uri": "",
        }

    videos, v_segments = [], []
    cur = 0
    for e in tl:
        dur = _us(e["end"] - e["start"])
        mat = video_material(img_paths[e["index"]], dur)
        videos.append(mat)
        v_segments.append(segment(mat["id"], cur, dur))
        cur += dur

    audios, a_segments_vo, a_segments_fx, a_segments_mu = [], [], [], []
    vo = audio_material(voice_dst, total_us, "Voiceover")
    audios.append(vo)
    a_segments_vo.append(segment(vo["id"], 0, total_us))
    if sfx_dst:
        fx = audio_material(sfx_dst, total_us, "SFX mix")
        audios.append(fx)
        a_segments_fx.append(segment(fx["id"], 0, total_us))
    if music_dst:
        try:
            from . import tts_service
            mu_dur = _us(tts_service.measure_duration(music_dst) or (tl[-1]["end"]))
        except Exception:
            mu_dur = total_us
        mu = audio_material(music_dst, min(mu_dur, total_us), "Background music")
        audios.append(mu)
        a_segments_mu.append(segment(mu["id"], 0, min(mu_dur, total_us)))

    texts, t_segments = [], []
    for cue in timeline_service.caption_cues(tl):
        mat = text_material(cue["text"])
        texts.append(mat)
        t_segments.append(segment(mat["id"], _us(cue["start"]), _us(cue["end"] - cue["start"]),
                                  render_index=10000))

    def track(ttype: str, segments: list) -> dict:
        return {"type": ttype, "segments": segments, "attribute": 0, "flag": 0,
                "id": _uid(), "is_default_name": True, "name": ""}

    tracks = [track("video", v_segments), track("audio", a_segments_vo)]
    if a_segments_fx:
        tracks.append(track("audio", a_segments_fx))
    if a_segments_mu:
        tracks.append(track("audio", a_segments_mu))
    tracks.append(track("text", t_segments))

    empty_material_lists = [
        "audio_balences", "audio_effects", "audio_fades", "audio_track_indexes", "beats",
        "canvases", "chromas", "color_curves", "digital_humans", "drafts", "effects", "flowers",
        "green_screens", "handwrites", "hsl", "images", "log_color_wheels", "loudnesses",
        "manual_deformations", "masks", "material_animations", "material_colors",
        "multi_language_refs", "placeholders", "plugin_effects", "primary_color_wheels",
        "realtime_denoises", "shapes", "smart_crops", "smart_relights",
        "sound_channel_mappings", "speeds", "stickers", "tail_leaders", "text_templates",
        "transitions", "video_effects", "video_trackings", "vocal_beautifys",
        "vocal_separations",
    ]
    materials = {"videos": videos, "audios": audios, "texts": texts}
    for k in empty_material_lists:
        materials[k] = []

    draft = {
        "canvas_config": {"height": H, "ratio": "original", "width": W},
        "color_space": 0,
        "config": {"adjust_max_index": 1, "attachment_info": [], "combination_max_index": 1,
                   "export_range": None, "extract_audio_last_index": 1, "lyrics_recognition_id": "",
                   "lyrics_sync": True, "lyrics_taskinfo": [], "maintrack_adsorb": True,
                   "material_save_mode": 0, "multi_language_current": "none",
                   "multi_language_list": [], "multi_language_main": "none",
                   "multi_language_mode": "none", "original_sound_last_index": 1,
                   "record_audio_last_index": 1, "sticker_max_index": 1,
                   "subtitle_keywords_config": None, "subtitle_recognition_id": "",
                   "subtitle_sync": True, "subtitle_taskinfo": [], "system_font_list": [],
                   "video_mute": False, "zoom_info_params": None},
        "create_time": now_ms // 1000, "duration": total_us, "extra_info": None,
        "fps": 30.0, "free_render_index_mode_on": False, "group_container": None,
        "id": _uid(), "keyframe_graph_list": [],
        "keyframes": {"adjusts": [], "audios": [], "effects": [], "filters": [], "handwrites": [],
                      "stickers": [], "texts": [], "videos": []},
        "last_modified_platform": {"app_id": 3704, "app_source": "cc", "app_version": "6.0.1",
                                   "device_id": "", "hard_disk_id": "", "mac_address": "",
                                   "os": "windows", "os_version": "10.0.22631"},
        "materials": materials, "mutable_config": None, "name": p["title"], "new_version": "120.0.0",
        "platform": {"app_id": 3704, "app_source": "cc", "app_version": "6.0.1", "device_id": "",
                     "hard_disk_id": "", "mac_address": "", "os": "windows",
                     "os_version": "10.0.22631"},
        "relationships": [], "render_index_track_mode_on": True, "retouch_cover": None,
        "source": "default", "static_cover_image_path": "", "time_marks": None,
        "tracks": tracks, "update_time": now_ms // 1000, "version": 360000,
    }
    (folder / "draft_content.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8")

    draft_id = _uid()
    meta = {
        "draft_cloud_last_action_download": False, "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "", "draft_cover": "", "draft_deeplink": "",
        "draft_fold_path": str(folder.resolve()), "draft_id": draft_id,
        "draft_is_ai_packaging_used": False, "draft_is_ai_shorts": False,
        "draft_is_article_video_draft": False, "draft_is_from_deeplink": "false",
        "draft_is_invisible": False,
        "draft_json_file": str((folder / "draft_content.json").resolve()),
        "draft_materials": [{"type": 0, "value": []}],
        "draft_muti_language_name": "", "draft_name": name, "draft_new_version": "",
        "draft_proxy_path": "", "draft_real_name": "", "draft_removable_storage_device": "",
        "draft_root_path": str(root.resolve()), "draft_segment_extra_info": [],
        "draft_timeline_materials_size_": 0, "draft_type": "", "tm_draft_cloud_completed": "",
        "tm_draft_cloud_modified": 0, "tm_draft_create": now_ms, "tm_draft_modified": now_ms,
        "tm_duration": total_us,
    }
    (folder / "draft_meta_info.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    (folder / "README.txt").write_text(f"""EXPERIMENTAL CAPCUT DRAFT — {p['title']}

1. Quit CapCut.
2. Copy the folder "{name}" into your CapCut drafts directory, e.g.:
   Windows:  C:\\Users\\<you>\\AppData\\Local\\CapCut\\User Data\\Projects\\com.lveditor.draft\\
   macOS:    ~/Movies/CapCut Drafts/   (varies by version)
   IMPORTANT: copy the WHOLE folder and keep the /assets subfolder with it —
   the draft references those media paths (currently pointing inside this
   app's data folder; copying this whole folder to the drafts dir keeps them
   intact — do NOT move the media elsewhere).
3. Reopen CapCut → the project appears in the project list.

CapCut's draft format is undocumented and changes between versions. If the
draft doesn't open or looks wrong, use the CapCut Ready Package instead —
it's guaranteed to work.
""", encoding="utf-8")

    zip_path = shutil.make_archive(str(root.parent / name), "zip", root_dir=root, base_dir=name)
    record = {"kind": "draft", "file": f"exports/{Path(zip_path).name}",
              "created": time.time(), "scenes": len(tl),
              "draft_folder": str(folder.resolve())}
    q = store.get_project(pid)
    q["exports"]["draft"] = record
    store.save_project(q)
    return record
