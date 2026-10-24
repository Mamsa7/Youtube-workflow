"""Image generation across free/paid backends (ALL user-keyed, most free):
  - gemini       : Gemini image model (aistudio key; images only on paid tier)
  - imagen       : Imagen 3 via Gemini API (paid)
  - pollinations : pollinations.ai — free community models (flux etc.), needs
                   a FREE key from enter.pollinations.ai/keys (they went keyed)
  - openrouter   : OpenRouter image models — some free, key at openrouter.ai/keys
  - leonardo     : Leonardo.Ai API — token-based, needs any API plan
  - huggingface  : new HF Inference Providers router (free tier with hf token)
  - hfspace      : Gradio Spaces (Qwen-Image 2.1 etc.), free, occasional queue waits

All backends share one contract: generate(prompt, fmt, settings) -> PNG/JPEG bytes.
Failures are RuntimeError with plain-language, actionable messages.
"""
from __future__ import annotations

import base64
from pathlib import Path

# Pollinations free catalog (Image model ids). /models reports more, but these
# are the documented, reliable ones.
POLLINATIONS_MODELS = [
    "flux",      # default, high quality, ~1024x1024
    "turbo",     # faster, simpler
    "gptimage",  # GPT-Image-style
]


# ---------------------------------------------------------------- helpers
def _dims(fmt: str) -> tuple[int, int]:
    return (720, 1280) if str(fmt) == "9:16" else (1280, 720)


def _ratio_note(fmt: str) -> str:
    return "vertical 9:16 portrait" if str(fmt) == "9:16" else "widescreen 16:9 landscape"


def _b64(payload) -> bytes:
    if isinstance(payload, str):
        return base64.b64decode(payload)
    if isinstance(payload, dict):
        for k in ("bytesBase64Encoded", "b64_json", "data"):
            if payload.get(k):
                return base64.b64decode(payload[k])
    return base64.b64decode(str(payload))


# ------------------------------------------------------------------ Gemini
def _gen_gemini(prompt: str, fmt: str, settings: dict) -> bytes:
    from google import genai
    from google.genai import types
    key = (settings.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API key missing — Settings → Gemini key.")
    client = genai.Client(api_key=key)
    model = settings.get("image_model") or "gemini-2.5-flash-image"
    note = _ratio_note(fmt)
    try:
        resp = client.models.generate_content(
            model=model,
            contents=f"Generate a single {note} illustration exactly as described. {prompt}",
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
        )
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "API key" in msg or "401" in msg or "403" in msg:
            raise RuntimeError("Gemini rejected the key — check Settings → Gemini API key.") from e
        if "429" in msg or "quota" in msg.lower():
            raise RuntimeError(
                "Gemini free tier has NO image generation — images need a paid Gemini plan "
                "(billing in Google AI Studio), or use a free backend: Pollinations, HF, HF Space.") from e
        raise RuntimeError(f"Gemini image generation failed: {msg[:300]}") from e
    for part in (resp.candidates[0].content.parts if resp.candidates else []):
        blob = getattr(part, "inline_data", None)
        if blob and getattr(blob, "data", None):
            return bytes(blob.data)
    raise RuntimeError(
        f"Gemini model '{model}' returned no image (text-only reply?) — try another image "
        "model id in Settings.")


# ------------------------------------------------------------------ Imagen
def _gen_imagen(prompt: str, fmt: str, settings: dict) -> bytes:
    from google import genai
    from google.genai import types
    key = (settings.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API key missing — Settings → Gemini key.")
    client = genai.Client(api_key=key)
    model = settings.get("imagen_model") or "imagen-3.0-generate-002"
    try:
        resp = client.models.generate_images(
            model=model, prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                aspect_ratio="9:16" if str(fmt) == "9:16" else "16:9",
            ))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Imagen failed: {str(e)[:300]}") from e
    if resp.generated_images:
        return bytes(resp.generated_images[0].image.image_bytes)
    raise RuntimeError(f"Imagen model '{model}' returned no image.")


# ------------------------------------------------------------ Pollinations
def _pollinations_model(settings: dict) -> str:
    m = (settings.get("free_image_model") or "flux").strip().lower()
    if "/" in m:  # user pasted a Space id by mistake
        raise RuntimeError(
            f"'{m}' looks like a Space/backend id, not a Pollinations model. Pollinations "
            "takes simple ids like 'flux' or 'turbo' — fix Settings → Free image model.")
    return m or "flux"


def _gen_pollinations(prompt: str, fmt: str, settings: dict, log=None,
                      retries: int = 2) -> bytes:
    import requests
    model = _pollinations_model(settings)
    width, height = _dims(fmt)
    key = (settings.get("pollinations_api_key") or "").strip()
    if not key:
        raise RuntimeError(
            "Pollinations now requires a FREE API key — get one at enter.pollinations.ai/keys, "
            "then paste it into Settings → Pollinations API key.")
    url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt, safe='')}"
    params = {"model": model, "width": width, "height": height,
              "nologo": "true", "seed": 42, "key": key}
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=180)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Cannot reach Pollinations — check the internet connection. ({e})") from e
        if r.status_code in (401, 403):
            raise RuntimeError(
                "Pollinations rejected the API key — make sure it came from "
                "enter.pollinations.ai/keys and is pasted exactly.")
        if r.status_code == 404:
            raise RuntimeError(
                f"Pollinations has no model '{model}' (HTTP 404) — press 🔍 Browse Pollinations "
                "models in Settings and pick an id that exists (e.g. 'flux' or 'turbo').")
        if r.status_code == 429 and attempt < retries:
            if log:
                log(f"Pollinations busy (429) — retry {attempt + 1}/{retries} shortly…")
            import time as _t
            _t.sleep(4 + attempt * 3)
            continue
        if r.status_code == 429:
            raise RuntimeError("Pollinations free tier is rate-limiting — wait a minute and retry.")
        if r.status_code == 402:
            raise RuntimeError(
                "Pollinations refused with a paid-tier error — the free key only accesses the "
                "free community models (flux, turbo, …). Pick one of those in Settings.")
        last = r.status_code
        if r.ok and r.content and len(r.content) > 100:
            return r.content
        if attempt < retries:
            continue
    raise RuntimeError(
        f"Pollinations failed (HTTP {last}) after retries — generic failure. Try again or switch backend.")


# --------------------------------------------------------------- OpenRouter
def _gen_openrouter(prompt: str, fmt: str, settings: dict) -> bytes:
    from app.services import openrouter_service
    return openrouter_service.generate_image(
        prompt, (settings.get("openrouter_image_model") or "").strip() or "google/gemini-2.5-flash-image-preview",
        fmt, settings)


# ---------------------------------------------------------------- Leonardo
LEONARDO_BASE = "https://cloud.leonardo.ai/api/rest/v1"


def list_leonardo_models(settings: dict) -> list[dict]:
    import requests
    key = (settings.get("leonardo_api_key") or "").strip()
    if not key:
        raise RuntimeError("Set your Leonardo API key first (Settings → Image → Leonardo key), then press 🔍.")
    r = requests.get(LEONARDO_BASE + "/platformModels",
                     headers={"Authorization": "Bearer " + key, "accept": "application/json"}, timeout=30)
    if r.status_code == 401:
        raise RuntimeError("Leonardo rejected the API key (HTTP 401).")
    r.raise_for_status()
    j = r.json()
    models = j.get("custom_models") or j.get("platform_models") or []
    return [{"id": m.get("id"), "name": m.get("name"),
             "desc": (m.get("description") or "")[:120]} for m in models]


def _gen_leonardo(prompt: str, fmt: str, settings: dict, log=None) -> bytes:
    import time
    import requests
    key = (settings.get("leonardo_api_key") or "").strip()
    if not key:
        raise RuntimeError("Leonardo API key missing — Settings → Leonardo key.")
    model_id = (settings.get("leonardo_model_id") or "").strip()
    if not model_id:
        raise RuntimeError(
            "No Leonardo model selected — press 🔍 Browse Leonardo models in Settings and pick one "
            "(ids change often; the browser fetches them live from your account).")
    w, h = _dims(fmt)
    body = {"prompt": prompt, "modelId": model_id, "width": w, "height": h,
            "num_images": 1, "alchemy": False, "presetStyle": "ILLUSTRATION"}
    h_auth = {"Authorization": "Bearer " + key, "accept": "application/json"}
    try:
        r = requests.post(LEONARDO_BASE + "/generations", json=body,
                          headers={**h_auth, "Content-Type": "application/json"}, timeout=60)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Cannot reach Leonardo — check the internet connection. ({e})") from e
    if r.status_code == 401:
        raise RuntimeError("Leonardo rejected the API key (HTTP 401) — Settings → Leonardo key.")
    if r.status_code == 402 or "plan" in r.text.lower() and r.status_code == 400:
        raise RuntimeError(
            "Leonardo says the API plan/credits are insufficient (HTTP 402) — API access needs "
            "a paid Leonardo plan; free backends: Pollinations, HF, HF Space.")
    r.raise_for_status()
    j = r.json()
    gen_id = ((j.get("sdGenerationJob") or {}).get("generationId")
              or (j.get("generate") or {}).get("generationId"))
    if not gen_id:
        raise RuntimeError(f"Leonardo didn't accept the job: {str(j)[:200]}")
    for _ in range(40):  # poll up to ~2 min
        time.sleep(3)
        p = requests.get(f"{LEONARDO_BASE}/generations/{gen_id}", headers=h_auth, timeout=30)
        if not p.ok:
            continue
        gj = p.json().get("generations_by_pk") or {}
        imgs = gj.get("generated_images") or []
        if imgs and imgs[0].get("url"):
            ir = requests.get(imgs[0]["url"], timeout=60)
            ir.raise_for_status()
            return ir.content
    raise RuntimeError("Leonardo timed out before delivering the image — try again.")


# ---------------------------------------------------------- Hugging Face
HF_ROUTER = "https://router.huggingface.co/hf-inference/models"


def _gen_hf(prompt: str, fmt: str, settings: dict, log=None, retries: int = 3) -> bytes:
    import time
    import requests
    token = (settings.get("hf_api_key") or "").strip()
    if not token:
        raise RuntimeError(
            "Hugging Face token missing — create a FREE one at hf.co/settings/tokens, then "
            "paste it into Settings → Hugging Face token.")
    model = (settings.get("hf_image_model") or "stabilityai/stable-diffusion-xl-base-1.0").strip()
    w, h = _dims(fmt)
    url = f"{HF_ROUTER}/{model}"
    body = {"inputs": f"{_ratio_note(fmt)} illustration. {prompt}",
            "parameters": {"width": w, "height": h, "num_inference_steps": 25}}
    headers = {"Authorization": "Bearer " + token, "Accept": "image/png"}
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.post(url, json=body, headers=headers, timeout=180)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Cannot reach Hugging Face — check the internet connection. ({e})") from e
        if r.status_code == 401:
            raise RuntimeError("Hugging Face rejected the token (HTTP 401) — Settings → HF token.")
        if r.status_code == 503:
            try:
                est = (r.json() or {}).get("estimated_time")
            except Exception:
                est = None
            wait = min(int(est or 20), 60) + attempt * 5
            if log:
                log(f"HF model '{model}' loading — waiting ~{wait}s (cold start, free tier)…")
            time.sleep(wait)
            continue
        if r.status_code == 404:
            raise RuntimeError(
                f"Hugging Face router has no model '{model}' (HTTP 404) — press 🔍 Browse HF "
                "models in Settings and pick a real text-to-image id.")
        if r.status_code == 429:
            last_err = "Hugging Face free tier rate-limited — wait a minute and retry."
            time.sleep(6 + attempt * 4)
            continue
        if r.ok and r.content and len(r.content) > 200:
            return r.content
        last_err = f"HF returned HTTP {r.status_code}: {r.text[:200]}"
        break
    raise RuntimeError(last_err or "Hugging Face generation failed with no image returned.")


# -------------------------------------------------------------- HF Space
HF_SPACE_PRESETS = [
    ("Qwen/Qwen-Image-2.1", "Qwen Image 2.1 — best quality"),
    ("multimodalart/FLUX.1-dev", "FLUX.1 [dev] — strong all-round"),
]


def _gen_hfspace(prompt: str, fmt: str, settings: dict, log=None) -> bytes:
    try:
        import gradio_client  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "The HF Space backend needs the 'gradio_client' package — run: pip install gradio_client") from e
    from gradio_client import Client
    space = (settings.get("hf_space_id") or "Qwen/Qwen-Image-2.1").strip()
    hf_token = (settings.get("hf_api_key") or "").strip() or None
    w, h = _dims(fmt)
    try:
        client = Client(space, hf_token=hf_token, verbose=False)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"Could not open HF Space '{space}' — check the id (format: user/name) and try again. ({e})") from e
    if log:
        log(f"HF Space '{space}' — queued… (first hit can take ~30–90 s on free infra, that's normal)")
    prompt_full = f"{_ratio_note(fmt)} illustration. {prompt}"
    try:
        result = client.predict(prompt_full, api_name="/infer") if space.startswith("Qwen/") else client.predict(prompt_full)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "queue" in msg.lower() or "busy" in msg.lower():
            raise RuntimeError(
                f"HF Space '{space}' is busy/queuing — this is normal on the free tier; the app already "
                "retries internally. Give it up to ~90 s, do NOT spam Test.") from e
        raise RuntimeError(f"HF Space '{space}' failed: {msg[:300]}") from e
    # result is usually a path, tuple, or dict with a path
    path = None
    if isinstance(result, (str, Path)):
        path = result
    elif isinstance(result, (tuple, list)) and result:
        path = result[0]
    elif isinstance(result, dict):
        path = result.get("path") or result.get("file")
    if not path:
        raise RuntimeError(f"HF Space '{space}' returned an unexpected result shape: {str(result)[:200]}")
    with open(path, "rb") as fh:
        return fh.read()


# ---------------------------------------------------------------- facade
def generate(prompt: str, fmt: str, settings: dict, log=None) -> bytes:
    backend = (settings.get("image_backend") or "gemini").strip().lower()
    if backend == "gemini":
        return _gen_gemini(prompt, fmt, settings)
    if backend == "imagen":
        return _gen_imagen(prompt, fmt, settings)
    if backend == "pollinations":
        return _gen_pollinations(prompt, fmt, settings, log=log)
    if backend == "openrouter":
        return _gen_openrouter(prompt, fmt, settings)
    if backend == "leonardo":
        return _gen_leonardo(prompt, fmt, settings, log=log)
    if backend == "huggingface":
        return _gen_hf(prompt, fmt, settings, log=log)
    if backend == "hfspace":
        return _gen_hfspace(prompt, fmt, settings, log=log)
    raise RuntimeError(f"Unknown image backend '{backend}'.")


BACKEND_LABELS = {
    "gemini": "Gemini images (paid)",
    "imagen": "Imagen 3 (paid)",
    "pollinations": "Pollinations (free + free key)",
    "openrouter": "OpenRouter image (free/paid models)",
    "leonardo": "Leonardo AI (API plan)",
    "huggingface": "Hugging Face (free token)",
    "hfspace": "HF Space (free, queues)",
}


# ============================================================ legacy facade
import io as _io
import textwrap as _tw
from pathlib import Path as _P

from app.config import BIBLE_REFS_DIR as _BIBLE_REFS


def compose_prompt(bible: dict, visual: str, fmt: str) -> str:
    ar = "16:9" if fmt == "16:9" else "9:16"
    chars = "\n".join(f"- {c['name']}: {c['description']}" for c in (bible.get("characters") or []))
    parts = [
        f"Generate ONE {ar} illustration.",
        f"Art style: {bible.get('style')}.",
        f"Linework: {bible.get('linework')}.",
        f"Faces: {bible.get('faces')}.",
        f"Color palette: {bible.get('color')}.",
        f"Background: {bible.get('background')}.",
        f"Composition: {bible.get('composition')}.",
    ]
    if chars:
        parts.append("Recurring characters (keep perfectly consistent):\n" + chars)
    parts += [
        f"SCENE TO DRAW: {visual}",
        "Single scene only, no panels, no collage, no watermarks, no rendered text unless a comic symbol is essential.",
        f"Avoid: {bible.get('negative')}",
    ]
    return "\n".join(parts)


def bible_ref_paths(bible: dict, character_ids: list | None = None) -> list:
    """Style/character reference images to pass along as visual guidance."""
    paths = []
    for name in (bible.get("reference_images") or [])[:4]:
        p = _BIBLE_REFS / name
        if p.exists():
            paths.append(p)
    for ch in (bible.get("characters") or []):
        if ch.get("sheet"):
            p = _BIBLE_REFS / ch["sheet"]
            if p.exists():
                paths.append(p)
    return paths


def mock_image(prompt: str, fmt: str = "16:9") -> bytes:
    """Styled placeholder card so the whole pipeline is demoable offline."""
    import random
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    w, h = (1080, 1920) if fmt == "9:16" else (1920, 1080)
    seed = abs(hash(prompt)) % (2**31)
    rng = random.Random(seed)
    hue = rng.randint(0, 359)
    c1 = _hsl(hue, 42, 68)
    c2 = _hsl((hue + 40) % 360, 38, 24)
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        f = y / h
        col = tuple(int(c1[i] * (1 - f) + c2[i] * f) for i in range(3))
        for x in range(0, w, 4):
            for k in range(4):
                px[x + k, y] = col
    d = ImageDraw.Draw(img, "RGBA")
    for _ in range(7):
        bx, by = rng.randint(0, w), rng.randint(0, h)
        r = rng.randint(w // 12, w // 5)
        d.ellipse([bx - r, by - r, bx + r, by + r],
                  fill=(*_hsl((hue + rng.randint(-30, 30)) % 360, 45, rng.randint(30, 70)), 26))
    img = img.filter(ImageFilter.GaussianBlur(14))
    d = ImageDraw.Draw(img, "RGBA")
    d.rectangle([0, int(h * 0.78), w, h], fill=(0, 0, 0, 46))
    cx, cy = int(w * 0.5), int(h * 0.6)
    s = h // 11
    ink = (250, 250, 250, 235)
    line = max(4, h // 160)
    d.ellipse([cx - s, cy - 2 * s, cx + s, cy], outline=ink, width=line)
    d.line([cx, cy, cx, cy + 1.6 * s], fill=ink, width=line)
    arm_dy = rng.randint(-1, 1) * s // 2
    d.line([cx - s, cy + 0.4 * s + arm_dy, cx + s, cy + 0.4 * s - arm_dy], fill=ink, width=line)
    d.line([cx, cy + 1.6 * s, cx - 0.8 * s, cy + 2.8 * s], fill=ink, width=line)
    d.line([cx, cy + 1.6 * s, cx + 0.8 * s, cy + 2.8 * s], fill=ink, width=line)
    er = max(2, s // 7)
    d.ellipse([cx - 0.4 * s, cy - 1.3 * s, cx - 0.4 * s + er * 2, cy - 1.3 * s + er * 2], fill=ink)
    d.ellipse([cx + 0.2 * s, cy - 1.3 * s, cx + 0.2 * s + er * 2, cy - 1.3 * s + er * 2], fill=ink)
    font_big = _font(h // 26)
    font_small = _font(h // 44)
    wrapped = _tw.wrap(prompt, width=46)[:4]
    card_h = int(h * 0.09) + len(wrapped) * (h // 38 + 6)
    card_y = h - card_h - int(h * 0.04)
    d.rounded_rectangle([w * 0.06, card_y, w * 0.94, card_y + card_h],
                        radius=h // 40, fill=(12, 14, 18, 190))
    d.text((w * 0.09, card_y + h * 0.018), "MOCK SCENE PREVIEW", font=font_small,
           fill=(255, 214, 130, 255))
    ty = card_y + int(h * 0.05)
    for ln in wrapped:
        d.text((w * 0.09, ty), ln, font=font_big, fill=(240, 240, 244, 255))
        ty += h // 38 + 6
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _hsl(h, s, l):
    import colorsys
    r, g, b = colorsys.hls_to_rgb(h / 360.0, l / 100.0, s / 100.0)
    return int(r * 255), int(g * 255), int(b * 255)


def _font(size: int):
    from PIL import ImageFont
    for cand in ("DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(cand, size)
        except Exception:
            pass
    return ImageFont.load_default()


def generate_image(prompt: str, settings: dict, fmt: str = "16:9",
                   ref_paths: list | None = None, log=None) -> bytes:
    """V1-compatible entry point used by the workflow engine."""
    if settings.get("mock_mode"):
        return mock_image(prompt, fmt)
    return generate(prompt, fmt, settings, log=log)
