"""Image generation: Gemini-native image models or Imagen (selectable in
Settings), with fully automatic Visual-Bible style injection so every image
matches the channel identity. Mock mode renders local placeholder cards.
"""
from __future__ import annotations

import io
import textwrap
from pathlib import Path

from ..config import BIBLE_REFS_DIR


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


def generate_image(prompt: str, settings: dict, fmt: str = "16:9",
                   ref_paths: list | None = None) -> bytes:
    """Returns PNG bytes."""
    if settings.get("mock_mode"):
        return mock_image(prompt, fmt)
    from .gemini_service import get_client
    client = get_client(settings)
    if settings.get("image_backend") == "imagen":
        return _gen_imagen(client, settings.get("imagen_model", "imagen-3.0-generate-002"), prompt, fmt)
    return _gen_gemini(client, settings.get("image_model", "gemini-2.5-flash-image"),
                       prompt, fmt, ref_paths or [])


def _aspect(fmt: str) -> str:
    return "9:16" if fmt == "9:16" else "16:9"


def _gen_gemini(client, model: str, prompt: str, fmt: str, ref_paths: list) -> bytes:
    from google.genai import types
    contents: list = [prompt]
    for rp in ref_paths:
        p = Path(rp)
        if p.exists():
            mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
            contents.append(types.Part.from_bytes(data=p.read_bytes(), mime_type=mime))
    config = None
    for attempt in (
        lambda: types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"],
                                            image_config=types.ImageConfig(aspect_ratio=_aspect(fmt))),
        lambda: types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
        lambda: None,
    ):
        try:
            config = attempt()
            break
        except Exception:
            continue
    kw = {"model": model, "contents": contents}
    if config is not None:
        kw["config"] = config
    resp = client.models.generate_content(**kw)
    for cand in (getattr(resp, "candidates", None) or []):
        for part in (cand.content.parts or []):
            data = getattr(getattr(part, "inline_data", None), "data", None)
            if data:
                return data
    raise RuntimeError("Image model returned no image — check the model name in Settings.")


def _gen_imagen(client, model: str, prompt: str, fmt: str) -> bytes:
    from google.genai import types
    r = client.models.generate_images(
        model=model, prompt=prompt,
        config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio=_aspect(fmt)))
    img = r.generated_images[0].image
    data = getattr(img, "image_bytes", None)
    if data:
        return data
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


# ------------------------------------------------------------------- mock
def mock_image(prompt: str, fmt: str = "16:9") -> bytes:
    """Styled placeholder card so the whole pipeline is demoable offline."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    w, h = (1080, 1920) if fmt == "9:16" else (1920, 1080)
    seed = abs(hash(prompt)) % (2**31)
    import random
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

    # soft vignette blobs
    for _ in range(7):
        bx, by = rng.randint(0, w), rng.randint(0, h)
        r = rng.randint(w // 12, w // 5)
        d.ellipse([bx - r, by - r, bx + r, by + r],
                  fill=(*_hsl((hue + rng.randint(-30, 30)) % 360, 45, rng.randint(30, 70)), 26))
    img = img.filter(ImageFilter.GaussianBlur(14))
    d = ImageDraw.Draw(img, "RGBA")

    # ground band
    d.rectangle([0, int(h * 0.78), w, h], fill=(0, 0, 0, 46))

    # a little stick character for personality
    cx, cy = int(w * 0.5), int(h * 0.6)
    s = h // 11
    ink = (250, 250, 250, 235)
    line = max(4, h // 160)
    d.ellipse([cx - s, cy - 2 * s, cx + s, cy], outline=ink, width=line)           # head
    d.line([cx, cy, cx, cy + 1.6 * s], fill=ink, width=line)                        # body
    arm_dy = rng.randint(-1, 1) * s // 2
    d.line([cx - s, cy + 0.4 * s + arm_dy, cx + s, cy + 0.4 * s - arm_dy], fill=ink, width=line)
    d.line([cx, cy + 1.6 * s, cx - 0.8 * s, cy + 2.8 * s], fill=ink, width=line)    # legs
    d.line([cx, cy + 1.6 * s, cx + 0.8 * s, cy + 2.8 * s], fill=ink, width=line)
    er = max(2, s // 7)
    d.ellipse([cx - 0.4 * s, cy - 1.3 * s, cx - 0.4 * s + er * 2, cy - 1.3 * s + er * 2], fill=ink)
    d.ellipse([cx + 0.2 * s, cy - 1.3 * s, cx + 0.2 * s + er * 2, cy - 1.3 * s + er * 2], fill=ink)

    # caption card
    font_big = _font(h // 26)
    font_small = _font(h // 44)
    wrapped = textwrap.wrap(prompt, width=46)[:4]
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

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _hsl(h, s, l):
    import colorsys
    r, g, b = colorsys.hls_to_rgb(h / 360, l / 100, s / 100)
    return int(r * 255), int(g * 255), int(b * 255)


def _font(size: int):
    from PIL import ImageFont
    for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(cand, size)
        except Exception:
            continue
    return ImageFont.load_default()


def bible_ref_paths(bible: dict, character_ids: list | None = None) -> list[Path]:
    """Style/character reference images to pass along as visual guidance."""
    paths = []
    for name in (bible.get("reference_images") or [])[:4]:
        p = BIBLE_REFS_DIR / name
        if p.exists():
            paths.append(p)
    for ch in (bible.get("characters") or []):
        if ch.get("sheet"):
            p = BIBLE_REFS_DIR / ch["sheet"]
            if p.exists():
                paths.append(p)
    return paths
