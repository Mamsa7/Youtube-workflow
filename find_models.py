"""Model discovery helper — prints the text/image models each configured
provider actually exposes, so Settings never has to guess ids.

    python find_models.py            # all configured providers
    python find_models.py gemini     # just one

Reads keys from data/settings.json (same as the running app).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def load_settings() -> dict:
    p = ROOT / "data" / "settings.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def gemini(settings: dict) -> None:
    print("\n== GEMINI ==")
    key = (settings.get("gemini_api_key") or "").strip()
    if not key:
        print("  (no gemini_api_key in data/settings.json)")
        return
    try:
        from google import genai
        client = genai.Client(api_key=key)
        models = [m.name.replace("models/", "") for m in client.models.list()]
        print("  text-ish:")
        for m in models:
            if "gemini" in m and "image" not in m:
                print("   -", m)
        print("  image-ish:")
        for m in models:
            if "image" in m or "imagen" in m:
                print("   -", m)
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR: {e}")


def openrouter(settings: dict) -> None:
    print("\n== OPENROUTER ==")
    try:
        from app.services import openrouter_service
        catalog = openrouter_service.list_models(settings)
        c = openrouter_service.classify(catalog)
        print(f"  {c['count']} models total — {len(c['text_free'])} free text, {len(c['image'])} image")
        print("  FREE text (first 15):")
        for m in c["text_free"][:15]:
            print("   -", m)
        print("  Image models (first 10):")
        for m in c["image"][:10]:
            print("   -", m)
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR: {e}")


def huggingface(settings: dict) -> None:
    print("\n== HUGGING FACE (Inference Providers router) ==")
    token = (settings.get("hf_api_key") or "").strip()
    if not token:
        print("  (no hf_api_key — free token from hf.co/settings/tokens)")
        return
    try:
        import requests
        r = requests.get(
            "https://huggingface.co/api/models?pipeline_tag=text-to-image&sort=likes&direction=-1&limit=15",
            headers={"Authorization": f"Bearer {token}"}, timeout=20)
        r.raise_for_status()
        for m in r.json():
            print(f"   - {m['id']}  (likes {m.get('likes', 0)})")
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR: {e}")


def elevenlabs(settings: dict) -> None:
    print("\n== ELEVENLABS ==")
    if not (settings.get("elevenlabs_api_key") or "").strip():
        print("  (no elevenlabs_api_key)")
        return
    try:
        from app.services import tts_service
        for v in tts_service.list_elevenlabs_voices(settings):
            print(f"   - {v['name']}  →  {v['id']}  [{v.get('category', '')}]")
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR: {e}")


def voicestudio(settings: dict) -> None:
    print("\n== VOICESTUDIO ==")
    if not (settings.get("voicestudio_url") or "").strip():
        print("  (no voicestudio_url — run VoiceStudio locally, set its URL in Settings)")
        return
    try:
        from app.services import tts_service
        info = tts_service.voicestudio_discover(settings)
        if not info.get("reachable"):
            print(f"  nothing answering at {info['url']}")
            return
        print(f"  service: {info.get('service') or '?'} {info.get('version') or ''}")
        print(f"  engines: {', '.join(str(e.get('id', e)) if isinstance(e, dict) else str(e) for e in info['engines']) or '?'}")
        print(f"  voices:  {', '.join(str(v.get('id', v.get('name', v))) if isinstance(v, dict) else str(v) for v in info['voices']) or 'default'}")
        print(f"  features: {', '.join(info['features']) or '—'}")
    except Exception as e:  # noqa: BLE001
        print(f"  ERROR: {e}")


PROVIDERS = {"gemini": gemini, "openrouter": openrouter, "huggingface": huggingface,
             "elevenlabs": elevenlabs, "voicestudio": voicestudio}


def main() -> None:
    settings = load_settings()
    which = sys.argv[1:] or list(PROVIDERS)
    for name in which:
        fn = PROVIDERS.get(name.lower())
        if fn:
            fn(settings)
        else:
            print(f"unknown provider '{name}' — choices: {', '.join(PROVIDERS)}")
    print()


if __name__ == "__main__":
    main()
