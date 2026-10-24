"""Gemini-side text helpers: script generation / revision, visual prompts,
SFX line tagging, timeline planning, plus the Settings ☤ 'Test X' probes.
Text can freely route through OpenRouter (free models) — Settings → Text provider.
"""
from __future__ import annotations

import json
import re

from app.services import openrouter_service

# ------------------------------------------------------------------ styles
NARRATION_STYLES = {
    "documentary": "Serious, informative documentary tone; measured pacing, precise facts.",
    "storytime": "Warm, friendly 'story time' storytelling — personal, engaging, a bit whimsical.",
    "kids": "Simple words, bouncy energy, short sentences, playful questions (children's story).",
    "newsreel": "Fast, crisp vintage 'newsreel announcer' cadence with punchy headlines.",
    "mystery": "Hushed, suspenseful true-crime/mystery narration with dramatic pauses and reveals.",
    "advert": "Upbeat, punchy commercial voice — big energy, key words emphasised.",
    "audiobook": "Steady, intimate audiobook narration — even pace, subtle character colouring.",
    "podcast": "Relaxed, conversational podcast host — natural rhythm, occasional asides.",
}

SYSTEM_BASE = (
    "You write YouTube narration scripts that are spoken aloud by text-to-speech. "
    "Write as continuous spoken prose (NO stage directions, NO brackets, NO markdown, "
    "NO scene numbers) unless asked otherwise. Sentences should sound natural when read. "
    "Language is English unless the user explicitly requests another. "
    "Never invent statistics; if unsure, stay qualitative."
)


# ------------------------------------------------------------- client utils
def _use_openrouter(settings: dict) -> bool:
    return (settings.get("text_provider") or "gemini").lower() == "openrouter"


def _models_from_settings(settings: dict) -> dict:
    return {"text": (settings.get("text_model") or "gemini-2.0-flash").strip(),
            "or_text": (settings.get("openrouter_text_model") or "openrouter/free").strip()}


def _gen_text(system: str, user: str, settings: dict, temperature: float = 0.8,
              max_tokens: int = 8192) -> str:
    if _use_openrouter(settings):
        return openrouter_service.chat(settings, _models_from_settings(settings)["or_text"],
                                       system, user, temperature, max_tokens)
    from google import genai
    from google.genai import types
    key = (settings.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError(
            "Gemini API key missing — set it in Settings, or switch Text provider to "
            "OpenRouter (free models; key from openrouter.ai/keys).")
    client = genai.Client(api_key=key)
    try:
        resp = client.models.generate_content(
            model=_models_from_settings(settings)["text"],
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system, temperature=temperature, max_output_tokens=max_tokens),
        )
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "429" in msg or "quota" in msg.lower():
            raise RuntimeError(
                "Gemini free text quota hit (HTTP 429) — wait a bit, or switch Text provider to "
                "OpenRouter's free models in Settings.") from e
        if "404" in msg or "not found" in msg.lower():
            raise RuntimeError(
                f"Gemini has no model '{_models_from_settings(settings)['text']}' — press "
                "🔍 Browse models in Settings and pick an id that exists.") from e
        raise RuntimeError(f"Gemini text failed: {msg[:300]}") from e
    return (resp.text or "").strip()


# ---------------------------------------------------------------- scripts
def generate_script(topic: str, minutes: float, fmt: str, style: str,
                    settings: dict, notes: str = "") -> str:
    style_desc = NARRATION_STYLES.get(style, NARRATION_STYLES["documentary"])
    target_words = int(minutes * 150)
    ratio = "vertical 9:16 (Short/TikTok)" if fmt == "9:16" else "widescreen 16:9"
    user = (
        f"Write a complete spoken narration script for a {minutes:.1f}-minute {ratio} YouTube video.\n"
        f"Topic: {topic}\n"
        f"Narration style: {style} — {style_desc}\n"
        f"Target length: about {target_words} words (150 words/min rule of thumb).\n"
        "Structure it as a gripping opening hook, clearly sequenced body, and a strong closing beat. "
        "No titles, no headings — spoken prose only.\n"
    )
    if notes.strip():
        user += f"Extra instructions from the creator:\n{notes}\n"
    out = _gen_text(SYSTEM_BASE, user, settings, temperature=0.85)
    return re.sub(r"\[.*?\]|\(.*?\)", "", out).strip()  # strip any sneaked-in stage directions


def revise_script(script: str, instruction: str, style: str, settings: dict) -> str:
    user = (
        f"Here is an existing YouTube narration script:\n\n{script}\n\n"
        f"Revise it per this instruction: {instruction}\n"
        f"Keep the {style} narrative style. Return ONLY the revised script as spoken prose."
    )
    out = _gen_text(SYSTEM_BASE, user, settings, temperature=0.7)
    return re.sub(r"\[.*?\]|\(.*?\)", "", out).strip()


def split_into_shots(script: str, max_shots: int, settings: dict) -> list[str]:
    """Split narration into visual beats, capped by the density cap. Falls back
    to a deterministic sentence-group split if the model drifts."""
    user = (
        "You are breaking a narration script into visual 'shots' — each shot is a piece of narration "
        "that works while ONE still image is on screen with a slow pan/zoom.\n"
        "Rules: keep EVERY word of the script, in order; do not rewrite; group 1–3 sentences per shot; "
        f"return AT MOST {max_shots} shots as a JSON array of strings.\n"
        "Script:\n" + script
    )
    out = _gen_text(SYSTEM_BASE, user, settings, temperature=0.2, max_tokens=8192)
    shots = _parse_json_array(out)
    if not shots:
        shots = _deterministic_split(script, max_shots)
    shots = [s.strip() for s in shots if str(s).strip()][:max_shots]
    if not shots:
        shots = [script]
    return shots


def _deterministic_split(script: str, max_shots: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", script.strip())
    if len(sentences) <= max_shots:
        return [s for s in sentences if s]
    per = max(1, len(sentences) // max_shots)
    shots = [" ".join(sentences[i:i + per + 1]) for i in range(0, len(sentences), per + 1)]
    return [s for s in shots if s][:max_shots]


def visual_prompt(shot_text: str, bible: dict, settings: dict) -> str:
    style_line = (f"Style: {bible.get('style')}; line: {bible.get('linework')}; "
                  f"faces: {bible.get('faces')}; color: {bible.get('color')}; "
                  f"background: {bible.get('background')}; composition: {bible.get('composition')}.")
    neg = bible.get("negative") or ""
    chars = "; ".join(f"{c.get('name')}: {c.get('description')}" for c in bible.get("characters", []))
    user = (
        "Write a single concise image-generation prompt (max 70 words) for the image that should "
        "sit under this narration beat:\n" + shot_text + "\n"
        + style_line + (f"\nCast: {chars}." if chars else "")
        + f"\nAvoid: {neg}." if neg else ""
    )
    prompt = _gen_text("You write image-generation prompts.", user, settings,
                       temperature=0.5, max_tokens=200)
    return prompt.strip().strip('"')


def improve_visual_prompt(prompt: str, bible: dict, settings: dict) -> str:
    user = (
        "Improve this image prompt for clearer composition and stronger storybook appeal, "
        "in at most 80 words. Keep the core subject identical. Style constraints:\n"
        f"{bible.get('style')}, {bible.get('linework')}, {bible.get('color')}.\n"
        f"Current prompt: {prompt}"
    )
    return _gen_text("You improve image prompts.", user, settings, temperature=0.6, max_tokens=256).strip()


def tag_sfx_beats(script: str, sfx_catalog: list[str], settings: dict,
                  max_tags: int = 12) -> list[dict]:
    """Return [{"cue_index": int, "sfx": "filename", "volume": 0-1}] – keeps dry
    when no catalog is available."""
    if not sfx_catalog:
        return []
    catalog = "\n".join(f"- {s}" for s in sfx_catalog[:80])
    user = (
        "Mark sound-effect cues in this narration script. Return JSON list of objects "
        '{"cue_index": n, "sfx": "filename", "volume": 0.0-1.0} where cue_index is the '
        f"0-based sentence index where the sound starts. Use only files from this library, "
        f"max {max_tags} cues, and only where a cue truly adds punch:\n{catalog}\n\nScript:\n{script}"
    )
    out = _gen_text(SYSTEM_BASE, user, settings, temperature=0.3, max_tokens=2048)
    data = _parse_json(out) or []
    return [d for d in data if isinstance(d, dict) and d.get("sfx") in sfx_catalog][:max_tags]


def test_text(settings: dict) -> str:
    out = _gen_text(SYSTEM_BASE, "Reply with exactly: READY", settings, temperature=0.0, max_tokens=8)
    prov = "OpenRouter" if _use_openrouter(settings) else "Gemini"
    model = _models_from_settings(settings)["or_text" if _use_openrouter(settings) else "text"]
    return f"OK — {prov} model '{model}' responded: {out[:40] or '(empty reply)'}"


def test_image(settings: dict) -> str:
    from . import image_service
    data = image_service.generate_image(
        "A simple smiley face doodle, black outline on white background",
        settings, fmt="16:9", ref_paths=[])
    return f"OK — received {len(data) // 1024} KB image"


def test_tts(settings: dict, tmp_dir) -> str:
    from . import tts_service
    out = tmp_dir / "tts_test"
    info = tts_service.synth_beat("StickStory Studio voice test, one two three.",
                                  out, {**settings, "mock_mode": False})
    f = tmp_dir / info["file"]
    dur = tts_service.measure_duration(f)
    if not f.exists() or dur <= 0:
        raise RuntimeError("TTS produced no audio")
    f.unlink(missing_ok=True)
    return f"OK — {info['provider']} produced {dur:.1f}s of audio"


def list_available_models(settings: dict) -> dict:
    """Report which text models are reachable for the Settings browser."""
    report: dict = {"provider": "openrouter" if _use_openrouter(settings) else "gemini",
                    "models": []}
    if _use_openrouter(settings):
        try:
            catalog = openrouter_service.list_models(settings)
            cls = openrouter_service.classify(catalog)
            report["models"] = cls["text_free"][:30] + cls["text_paid"][:20]
            report["counts"] = {"total": cls["count"],
                                "free": len(cls["text_free"]),
                                "image": len(cls["image"])}
            report["image_models"] = cls["image"][:20]
        except Exception as e:  # noqa: BLE001
            report["error"] = str(e)
    else:
        key = (settings.get("gemini_api_key") or "").strip()
        if not key:
            report["error"] = "No Gemini key set"
        else:
            try:
                from google import genai
                client = genai.Client(api_key=key)
                names = [m.name.replace("models/", "") for m in client.models.list()]
                report["models"] = [n for n in names if "gemini" in n][:40]
            except Exception as e:  # noqa: BLE001
                report["error"] = str(e)
    return report


# --------------------------------------------------------------- json utils
def _parse_json(raw: str):
    m = re.search(r"[\[{].*[\]}]", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _parse_json_array(raw: str) -> list | None:
    v = _parse_json(raw)
    return v if isinstance(v, list) else None


# ==================================================== workflow-engine API
# Structured-JSON helpers required by app/services/workflow.py (script
# generation from a source transcript, sfx planning, mock variants).

SAMPLE_TRANSCRIPT = """Welcome back to the channel. Today we are looking at why tiny daily habits
beat big once-a-year resolutions. Researchers tracked two groups for a year:
one set huge January goals, the other committed to two-minute daily actions.
After twelve months the daily group outperformed on every measure — reading,
fitness, savings. The reason is friction: big goals demand motivation every
time, tiny habits sneak under it. We will cover the two-minute rule, habit
stacking, and the comeback plan for missed days."""


def get_client(settings: dict):
    from google import genai
    key = (settings.get("gemini_api_key") or "").strip()
    if not key:
        raise RuntimeError("Gemini API key not set — add it in Settings or enable Mock mode.")
    return genai.Client(api_key=key)


def extract_json(text: str):
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    for candidate in (t, t[t.find("{"):t.rfind("}") + 1] if "{" in t else t,
                      t[t.find("["):t.rfind("]") + 1] if "[" in t else t):
        try:
            return json.loads(candidate)
        except Exception:
            continue
    raise ValueError(f"Model did not return valid JSON: {t[:200]}")


def _gen_json_text(prompt: str, settings: dict, temperature: float = 0.8):
    """JSON generation routed through the configured text provider."""
    if _use_openrouter(settings):
        raw = openrouter_service.chat(
            settings, _models_from_settings(settings)["or_text"],
            "You answer ONLY with strict JSON — no prose, no markdown fences.",
            prompt, temperature=temperature, max_tokens=8192)
        return extract_json(raw)
    client = get_client(settings)
    model = (settings.get("text_model") or "gemini-2.0-flash").strip()
    from google.genai import types
    try:
        resp = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json",
                                               temperature=temperature))
    except Exception:
        resp = client.models.generate_content(model=model, contents=prompt)
    return extract_json(getattr(resp, "text", "") or "")


def style_block(bible: dict) -> str:
    chars = bible.get("characters") or []
    char_txt = "\n".join(f"- {c['name']}: {c['description']}" for c in chars) or "- (none defined yet)"
    return (
        f"ART STYLE BIBLE for this YouTube channel (apply to every visual):\n"
        f"Style: {bible.get('style')}\n"
        f"Linework: {bible.get('linework')}\n"
        f"Faces: {bible.get('faces')}\n"
        f"Colors: {bible.get('color')}\n"
        f"Backgrounds: {bible.get('background')}\n"
        f"Composition: {bible.get('composition')}\n"
        f"Recurring characters:\n{char_txt}\n"
        f"Avoid: {bible.get('negative')}"
    )


def build_script_prompt(transcript: str, opts: dict, bible: dict) -> str:
    from app import config as _cfg
    dens = _cfg.DENSITIES.get(opts.get("density") or "normal", _cfg.DENSITIES["normal"])
    fmt = "vertical 9:16 (YouTube Short/TikTok)" if opts.get("format") == "9:16" else "widescreen 16:9"
    style = bible.get("style_name") or "Storybook Stick"
    narr_style = opts.get("narration_style") or "documentary"
    narr = NARRATION_STYLES.get(narr_style, NARRATION_STYLES["documentary"])
    return f"""You are writing a narrated YouTube video script WITH matching visuals.

SOURCE MATERIAL (adapt faithfully — never invent competing facts):
\"\"\"
{transcript[:12000]}
\"\"\"

FORMAT: {fmt}. Target length: {opts.get('target_length_min', 5)} minutes of narration
(~{int(float(opts.get('target_length_min', 5)) * 150)} spoken words total).
NARRATION STYLE: {narr_style} — {narr}
PACING: {dens['label']} — split into at most {dens['cap']} beats.

For EVERY beat output: (1) 'narration' = the exact spoken text for that beat
(1-3 natural sentences, continuous prose, no directions), (2) 'visual' = a
concrete description of what the illustration shows while it is spoken.

{style_block(bible)}

Also produce: 'title' (clickable, honest), 'description' (2-3 sentences +
hashtags), 'tags' (up to 12), 'fact_check' (up to 6 claims a creator should verify).

Return ONLY JSON:
{{"title": "...", "description": "...", "tags": [...], "fact_check": [...],
  "beats": [{{"narration": "...", "visual": "..."}}, ...]}}"""


def generate_script(transcript: str, opts: dict, bible: dict, settings: dict) -> dict:  # noqa: F811
    data = _gen_json_text(build_script_prompt(transcript, opts, bible), settings)
    return _normalize_script(data)


def _normalize_script(data: dict) -> dict:
    beats = []
    for i, b in enumerate(data.get("beats") or []):
        narration = (b.get("narration") or "").strip()
        visual = (b.get("visual") or "").strip()
        if not narration:
            continue
        beats.append({"id": f"b{i + 1:03d}", "narration": narration, "visual": visual})
    if not beats:
        raise ValueError("Model returned a script with no beats — try again.")
    return {
        "title": (data.get("title") or "Untitled").strip(),
        "description": (data.get("description") or "").strip(),
        "tags": [str(t) for t in (data.get("tags") or [])][:12],
        "fact_check": [str(t) for t in (data.get("fact_check") or [])][:6],
        "beats": beats,
    }


def generate_script_mock(transcript: str, opts: dict, bible: dict) -> dict:
    src = (transcript or SAMPLE_TRANSCRIPT).strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", src) if s.strip()]
    from app import config as _cfg
    dens = _cfg.DENSITIES.get(opts.get("density") or "normal", _cfg.DENSITIES["normal"])
    cap = min(dens["cap"], max(4, len(sentences)))
    per = max(1, len(sentences) // cap)
    beats = []
    for i in range(0, len(sentences), per):
        group = " ".join(sentences[i:i + per + 1])
        beats.append({
            "narration": group,
            "visual": f"A storybook stick-figure scene illustrating: {group[:90]}",
        })
        if len(beats) >= cap:
            break
    return _normalize_script({
        "title": "Tiny Habits, Big Results (Demo)",
        "description": "A mocked demo script — turn off Mock mode and add a key for the real thing. #demo",
        "tags": ["demo", "habits", "storybook"],
        "fact_check": ["Demo content — no real claims."],
        "beats": beats,
    })


def generate_sfx_plan(beats: list[dict], timeline: list[dict], settings: dict) -> list[dict]:
    """AI decides where sound effects should land. Returns cue dicts."""
    listing = "\n".join(
        f"Scene {i + 1} [{t['start']:.2f}s–{t['end']:.2f}s]: {b['narration'][:140]}"
        for i, (b, t) in enumerate(zip(beats, timeline)))
    prompt = f"""You are the sound designer for a 2D animated educational YouTube video.
For the scenes below (with voiceover timestamps), decide tasteful sound effects:
scene transitions, accent hits for reveals, subtle ambience, and comedic beats.
RULES: max 2 cues per scene, max 40 cues total, never under fast dialogue punchlines,
prefer restraint. Use short lowercase names like: whoosh, pop, ding, suspense, boom, sparkle.

SCENES:
{listing[:9000]}

Return ONLY JSON: {{"cues": [{{"scene": 1, "offset": 0.0, "name": "whoosh", "description": "transition"}}]}}
where offset is seconds after the scene start."""
    data = _gen_json_text(prompt, settings, temperature=0.6)
    cues = []
    for c in (data.get("cues") or [])[:60]:
        try:
            scene = int(c.get("scene", 1))
        except Exception:
            continue
        if not (1 <= scene <= len(beats)):
            continue
        cues.append({
            "beat": scene,
            "offset": max(0.0, float(c.get("offset", 0) or 0)),
            "name": str(c.get("name", "whoosh"))[:40],
            "source": "procedural",
            "description": str(c.get("description", ""))[:120],
            "gain": 0.8,
        })
    return cues


def generate_sfx_plan_mock(beats: list[dict], timeline: list[dict]) -> list[dict]:
    cues = []
    ambient_pool = ["birds", "wind", "crickets"]
    for i, _ in enumerate(beats):
        cues.append({"beat": i + 1, "offset": 0.0, "name": "whoosh",
                     "source": "procedural", "description": "scene transition", "gain": 0.55})
        if i % 3 == 1:
            cues.append({"beat": i + 1, "offset": 0.6, "name": "pop",
                         "source": "procedural", "description": "reveal accent", "gain": 0.7})
        if i % 4 == 2:
            cues.append({"beat": i + 1, "offset": 0.2, "name": ambient_pool[(i // 4) % 3],
                         "source": "procedural", "description": "scene ambience", "gain": 0.35})
    return cues[:40]
