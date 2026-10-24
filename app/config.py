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
    "text_model": "gemini-3.8-flash",
    "image_backend": "gemini",            # "gemini" | "imagen" | "pollinations" | "openrouter" | "leonardo" | "huggingface" | "hfspace"
    "image_model": "gemini-3.8-flash-image",
    "imagen_model": "imagen-3.0-generate-002",
    "free_image_model": "flux",           # Pollinations model id or alias (see 🔍 Browse Pollinations models)
    "pollinations_api_key": "",           # FREE key, required since Pollinations went keyed → enter.pollinations.ai/keys
    "leonardo_api_key": "",               # cloud.leonardo.ai → API access (token-based; needs a Leonardo API plan)
    "leonardo_model_id": "",              # pick via 🔍 Browse Leonardo models (ids change often — browser fetches live)
    "hf_api_key": "",                     # FREE Hugging Face token (hf.co/settings/tokens) — runs open models on HF's free tier
    "hf_image_model": "stabilityai/stable-diffusion-xl-base-1.0",
    "hf_space_id": "Qwen/Qwen-Image-2.1", # free hosted Gradio Space; no key needed, token avoids anonymous throttle
    "openrouter_api_key": "",
    "text_provider": "gemini",            # "gemini" | "openrouter"
    "openrouter_text_model": "openrouter/free",
    "openrouter_image_model": "google/gemini-2.5-flash-image-preview",
    "tts_provider": "edge-tts",           # "edge-tts" | "gtts" | "elevenlabs" | "voicestudio"
    "elevenlabs_api_key": "",             # user key — free tier ≈10k chars/month (elevenlabs.io)
    "elevenlabs_voice": "21m00Tcm4TlvDq8ikWAM",   # default: Rachel. Also accepts stock names / your custom voice names or ids
    "elevenlabs_model": "eleven_multilingual_v2",  # free accounts may need eleven_flash_v2_5 / eleven_turbo_v2_5
    "edge_voice": "en-US-GuyNeural",
    "edge_rate": "+0%",
    "edge_pitch": "+0Hz",
    "gtts_lang": "en",
    "gtts_tld": "com",                    # gTTS accent domain: com=US, co.uk, com.au, co.in, ng, gh, ke, co.za, ca, ie…
    "narration_style": "documentary",     # script tone — see gemini_service.NARRATION_STYLES
    # --- VoiceStudio (self-hosted OmniVoice speech service; LOCAL/LAN only) ---
    "voicestudio_url": "",                # e.g. http://127.0.0.1:3900 — never expose to the internet; key stays server-side
    "omnivoice_api_key": "",              # server-only bearer key for VoiceStudio; never sent to the browser (masked)
    "vs_engine_id": "omnivoice",          # speech engine id selected in VoiceStudio
    "vs_voice_profile_id": "",            # cloned/designed voice profile id ('' = VoiceStudio's 'default' voice)
    "vs_license_ack": False,              # REQUIRED: default OmniVoice weights are CC-BY-NC (non-commercial) — ack + approved engine/weights before production/monetized use
    "vs_fallback_enabled": True,          # if VoiceStudio errors mid-run, fall back to the existing narration provider
    "vs_fallback_provider": "edge-tts",   # which provider catches VoiceStudio failures
    "vs_share_pin": "",                   # optional 6-digit Network-share PIN (only for LAN/remote VoiceStudio; loopback needs nothing)
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

# ------------------------------------------------------------------ secrets
# These never travel to the browser: GET /api/settings masks them, and PUT
# treats the mask as "keep the stored value". Everything VoiceStudio-related
# happens strictly server-side.
SECRET_MASK = "••••••"
SECRET_KEYS = (
    "gemini_api_key", "openrouter_api_key", "pollinations_api_key",
    "leonardo_api_key", "hf_api_key", "elevenlabs_api_key", "omnivoice_api_key",
    "vs_share_pin",
)


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


def get_settings(public: bool = False) -> dict:
    with _lock:
        s = _load_json(SETTINGS_PATH, DEFAULT_SETTINGS)
    merged = {**DEFAULT_SETTINGS, **s}
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key and not merged.get("gemini_api_key"):
        merged["gemini_api_key"] = env_key
    if public:
        for k in SECRET_KEYS:
            if merged.get(k):
                merged[k] = SECRET_MASK
    return merged


def save_settings(settings: dict) -> dict:
    """Merge payload onto stored settings; masked secrets keep stored values."""
    with _lock:
        current = _load_json(SETTINGS_PATH, DEFAULT_SETTINGS)
    keep = {}
    for k, v in DEFAULT_SETTINGS.items():
        nv = settings.get(k, current.get(k, v))
        if k in SECRET_KEYS and nv == SECRET_MASK:
            nv = current.get(k, "")
        keep[k] = nv
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
