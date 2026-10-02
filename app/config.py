"""Persistent configuration: settings + the channel's Visual Bible.

Everything lives under ./data (git-ignored) so API keys and media never end
up in version control.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PROJECTS_DIR = DATA_DIR / "projects"
SFX_LIBRARY_DIR = DATA_DIR / "sfx_library"
BIBLE_REFS_DIR = DATA_DIR / "bible_refs"
SETTINGS_PATH = DATA_DIR / "settings.json"
BIBLE_PATH = DATA_DIR / "visual_bible.json"

for _d in (DATA_DIR, PROJECTS_DIR, SFX_LIBRARY_DIR, BIBLE_REFS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()

# Seconds-per-visual targets. Used together with the per-density image caps so
# long videos reuse images with Ken Burns movement instead of exploding cost.
DENSITIES = {
    "relaxed": {"secs": 8.0, "cap": 40, "label": "Relaxed — new visual ~8s"},
    "normal": {"secs": 5.0, "cap": 60, "label": "Normal — new visual ~5s"},
    "fast": {"secs": 3.0, "cap": 85, "label": "Fast — new visual ~3s"},
    "shorts": {"secs": 2.0, "cap": 40, "label": "Shorts — new visual ~2s"},
}

DEFAULT_SETTINGS = {
    "gemini_api_key": "",
    "text_model": "gemini-2.5-flash",
    "image_backend": "gemini",            # "gemini" (native image gen) | "imagen"
    "image_model": "gemini-2.5-flash-image",
    "imagen_model": "imagen-3.0-generate-002",
    "tts_provider": "edge-tts",           # "edge-tts" | "gtts"
    "edge_voice": "en-US-GuyNeural",
    "edge_rate": "+0%",
    "edge_pitch": "+0Hz",
    "gtts_lang": "en",
    "gtts_tld": "com",
    "mock_mode": False,
}

DEFAULT_BIBLE = {
    "style_name": "Storybook Stick",
    "style": "2D educational storybook cartoon, flat vector-style illustration",
    "linework": "clean dark outlines, thin stick-like limbs, simplified anatomy",
    "faces": "white simplified faces, oversized expressive eyes, exaggerated emotions",
    "color": "muted warm palette, earthy browns, greens and blues, soft cel shading",
    "background": "illustrated storybook environments with moderate detail, contextual props, soft lighting",
    "composition": ("clear silhouettes, characters large in frame, uncluttered focal point, "
                    "occasional comic symbols (sweat drops, impact stars, motion lines)"),
    "negative": "photorealistic, 3d render, cgi, blurry, watermark, text artifacts, extra limbs, deformed anatomy",
    "characters": [],        # {"id","name","description","sheet": filename|None}
    "reference_images": [],  # filenames inside BIBLE_REFS_DIR
}

# Popular voices shown instantly; the full live list can be fetched from Edge.
EDGE_VOICES_FALLBACK = [
    ("en-US-GuyNeural", "Guy · US male, narrator"),
    ("en-US-ChristopherNeural", "Christopher · US male, deep"),
    ("en-US-AndrewNeural", "Andrew · US male, warm"),
    ("en-US-BrianNeural", "Brian · US male, conversational"),
    ("en-US-EricNeural", "Eric · US male, neutral"),
    ("en-US-SteffanNeural", "Steffan · US male, youthful"),
    ("en-US-JennyNeural", "Jenny · US female"),
    ("en-US-AriaNeural", "Aria · US female, expressive"),
    ("en-US-MichelleNeural", "Michelle · US female"),
    ("en-US-AnaNeural", "Ana · US female, childlike"),
    ("en-GB-RyanNeural", "Ryan · UK male"),
    ("en-GB-OllieNeural", "Ollie · UK male, upbeat"),
    ("en-GB-SoniaNeural", "Sonia · UK female"),
    ("en-GB-LibbyNeural", "Libby · UK female"),
    ("en-AU-WilliamNeural", "William · AU male"),
    ("en-AU-NatashaNeural", "Natasha · AU female"),
    ("en-CA-LiamNeural", "Liam · CA male"),
    ("en-CA-ClaraNeural", "Clara · CA female"),
    ("en-IN-PrabhatNeural", "Prabhat · IN male"),
    ("en-IN-NeerjaNeural", "Neerja · IN female"),
]

GTTS_LANGS = [
    ("en", "English"), ("en-us", "English (US)"), ("en-gb", "English (UK)"),
    ("es", "Spanish"), ("fr", "French"), ("de", "German"), ("it", "Italian"),
    ("pt", "Portuguese"), ("hi", "Hindi"), ("ar", "Arabic"), ("ru", "Russian"),
    ("ja", "Japanese"), ("ko", "Korean"), ("zh-CN", "Chinese (Simplified)"),
]


def _load_json(path: Path, default):
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return json.loads(json.dumps(default))


def _save_json(path: Path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def get_settings() -> dict:
    with _lock:
        s = _load_json(SETTINGS_PATH, DEFAULT_SETTINGS)
    merged = {**DEFAULT_SETTINGS, **s}
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key and not merged.get("gemini_api_key"):
        merged["gemini_api_key"] = env_key
    return merged


def save_settings(settings: dict) -> dict:
    keep = {k: settings.get(k, v) for k, v in DEFAULT_SETTINGS.items()}
    with _lock:
        _save_json(SETTINGS_PATH, keep)
    return get_settings()


def get_bible() -> dict:
    with _lock:
        b = _load_json(BIBLE_PATH, DEFAULT_BIBLE)
    merged = {**DEFAULT_BIBLE, **b}
    merged.setdefault("characters", [])
    merged.setdefault("reference_images", [])
    return merged


def save_bible(bible: dict) -> dict:
    keep = {k: bible.get(k, v) for k, v in DEFAULT_BIBLE.items()}
    with _lock:
        _save_json(BIBLE_PATH, keep)
    return get_bible()
