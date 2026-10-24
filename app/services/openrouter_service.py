"""OpenRouter helpers: model catalog access, classification, chat and image
generation through OpenRouter's OpenAI-compatible gateway. Free models are
filtered/classified so users pick from real, working choices.
"""
from __future__ import annotations

BASE = "https://openrouter.ai/api/v1"


def _key(settings: dict) -> str:
    key = (settings.get("openrouter_api_key") or "").strip()
    if not key:
        raise RuntimeError(
            "OpenRouter API key not set — create a FREE account at openrouter.ai/keys "
            "and paste the key into Settings. Many models on OpenRouter are free "
            "(ids end in ':free').")
    return key


def list_models(settings: dict) -> list[dict]:
    """Live model catalog (unauthenticated endpoint, but key helps)."""
    import requests
    try:
        r = requests.get(BASE + "/models", timeout=30,
                         headers={"Authorization": "Bearer " + (settings.get("openrouter_api_key") or "")})
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Cannot reach OpenRouter — check the internet connection. ({e})") from e
    r.raise_for_status()
    return r.json().get("data") or []


def classify(models: list[dict]) -> dict:
    """Split the catalog into buckets the UI can offer.
    Free = pricing prompt+completion both '0' (or id ends ':free')."""
    out = {"text_free": [], "text_paid": [], "image": [], "count": len(models)}
    for m in models:
        mid = m.get("id") or ""
        pricing = m.get("pricing") or {}
        modal = (m.get("architecture") or {}).get("output_modalities") or []
        free = (str(pricing.get("prompt", "1")) in ("0", "0.0")
                and str(pricing.get("completion", "1")) in ("0", "0.0")) or mid.endswith(":free")
        if "image" in modal or "image" in (m.get("name") or "").lower():
            out["image"].append(mid)
        elif free:
            out["text_free"].append(mid)
        else:
            out["text_paid"].append(mid)
    for k in out:
        if isinstance(out[k], list):
            out[k].sort()
    return out


def chat(settings: dict, model: str, system: str, user: str,
         temperature: float = 0.8, max_tokens: int = 8192) -> str:
    """Plain text completion (OpenAI-compatible)."""
    import requests
    try:
        r = requests.post(
            BASE + "/chat/completions",
            headers={"Authorization": "Bearer " + _key(settings),
                     "HTTP-Referer": "http://localhost", "X-Title": "StickStory Studio"},
            json={"model": model, "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}],
                "temperature": temperature, "max_tokens": max_tokens},
            timeout=180)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Cannot reach OpenRouter — check the internet connection. ({e})") from e
    if r.status_code == 401:
        raise RuntimeError("OpenRouter rejected the API key (HTTP 401) — check Settings → OpenRouter key.")
    if r.status_code == 404:
        raise RuntimeError(
            f"OpenRouter has no model '{model}' (HTTP 404) — press 🔍 Browse OpenRouter "
            "models in Settings and pick one that exists (free ids end in ':free').")
    if r.status_code == 429:
        raise RuntimeError(
            "OpenRouter free model is rate-limited right now — wait a minute and retry, "
            "or pick a different free model in Settings.")
    r.raise_for_status()
    try:
        return r.json()["choices"][0]["message"]["content"] or ""
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("OpenRouter returned an unexpected response shape.") from e


def generate_image(prompt: str, model: str, fmt: str, settings: dict) -> bytes:
    """OpenRouter image models answer via the chat endpoint with
    modalities=['text','image']; bytes come back base64-embedded."""
    import base64 as _b64
    import requests
    ratio = "9:16" if fmt == "9:16" else "16:9"
    try:
        r = requests.post(
            BASE + "/chat/completions",
            headers={"Authorization": "Bearer " + _key(settings),
                     "HTTP-Referer": "http://localhost", "X-Title": "StickStory Studio"},
            json={"model": model,
                  "messages": [{"role": "user",
                                "content": f"Generate a single {ratio} illustration exactly as described. {prompt}"}],
                  "modalities": ["text", "image"]},
            timeout=240)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Cannot reach OpenRouter — check the internet connection. ({e})") from e
    if r.status_code == 404:
        raise RuntimeError(
            f"OpenRouter has no model '{model}' (HTTP 404) — press 🔍 Browse OpenRouter "
            "models in Settings and pick an image model from the list.")
    if r.status_code == 402:
        raise RuntimeError(
            "OpenRouter says this image model needs credits on the account — switch to a "
            "*free* image model (the Browse list marks them) or use the Pollinations / "
            "Hugging Face free backends.")
    if r.status_code == 429:
        raise RuntimeError(
            "OpenRouter free image model is rate-limited — wait a bit and retry, or switch "
            "to another free backend meanwhile.")
    r.raise_for_status()
    j = r.json()
    try:
        for part in j["choices"][0]["message"].get("images") or []:
            url = str(part.get("image_url", {}).get("url", ""))
            if url.startswith("data:"):
                return _b64.b64decode(url.split(",", 1)[1])
    except Exception:
        pass
    # last-ditch: search any data-uri in the message content
    import re as _re
    m = _re.search(r"data:image/[a-z0-9.+-]+;base64,([A-Za-z0-9+/=\s]+)", str(j)[:2_000_000])
    if m:
        return _b64.b64decode(m.group(1))
    raise RuntimeError(
        f"OpenRouter model '{model}' answered without an image — it may not support image "
        "generation; press 🔍 Browse OpenRouter models and pick a real image model.")
