"""Gemini integration: script engine, scene director, SFX planner.

Model names come from Settings so the app keeps working as Google changes
model availability — no hard-coded dependencies on specific versions.
"""
from __future__ import annotations

import json
import re

from ..config import DENSITIES

SAMPLE_TRANSCRIPT = """
Music is so universal that scientists have found it in every human culture ever
studied. But when did it actually begin? In 1995, archaeologists digging in a
cave in Slovenia found something extraordinary: a small flute carved from the
femur of a cave bear, dated to around 50,000 years ago. That means Neanderthals
— not even modern humans — may have been making music. Some researchers argue
the holes were made by animal teeth, and the debate still rages today. But the
story gets stranger. A bone flute found in Germany's Hohle Fels cave, made from
a vulture's wing bone, is over 40,000 years old and can still be played. Early
humans didn't just make music for fun. Rhythmic sound helped groups coordinate
work, remember stories before writing existed, and bond emotionally. Some
scientists believe music may even predate language itself — that we sang before
we spoke. And here's the twist: your brain treats music unlike almost any other
stimulus. It activates the same reward circuits as food and affection, releasing
dopamine even when a song just *anticipates* a drop. Every culture invents
instruments, every baby responds to lullabies, and every era thinks its music is
the best ever made. From a cave bear flute in the Stone Age to stadium
concerts today, the strange history of music is really the history of us.
"""


# ------------------------------------------------------------------ helpers
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


def _gen_json(client, model: str, prompt: str, temperature: float = 0.8):
    from google.genai import types
    try:
        resp = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json",
                                               temperature=temperature))
    except Exception:
        # Some models/SDK combos reject response_mime_type — retry plainly.
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


# ------------------------------------------------------------------ script
def build_script_prompt(transcript: str, opts: dict, bible: dict) -> str:
    fmt = opts.get("format", "16:9")
    minutes = float(opts.get("target_length_min", 8))
    density = DENSITIES.get(opts.get("density", "normal"), DENSITIES["normal"])
    total_secs = int(minutes * 60)
    wpm = 165 if fmt == "16:9" else 180
    target_words = int(minutes * wpm)
    n_beats = max(3, min(density["cap"], round(total_secs / density["secs"])))
    words_per_beat = max(12, round(target_words / n_beats))
    shorts = fmt == "9:16"

    if opts.get("script_mode") == "clone":
        task = (
            "Rewrite the reference transcript into a NEW narrated video script that follows its "
            "beat-by-beat structure, pacing and hook pattern closely, but rewords everything in "
            "original language so it is not a copy. Keep the same topic and factual spine."
        )
    else:
        task = (
            "Use the reference transcript ONLY as research. Extract its topic, hook, structural "
            "beats and key claims, then independently write an original narrated video treatment "
            "on the same subject with a fresh angle, new examples where possible, and none of "
            "the original sentences. Do NOT paraphrase line-by-line."
        )

    return f"""You are the head writer and scene director for a YouTube channel that makes
{"short-form vertical (YouTube Shorts)" if shorts else "long-form"} educational videos with
2D storybook stick-character animations.

REFERENCE TRANSCRIPT (source material):
\"\"\"
{transcript.strip()[:12000]}
\"\"\"

TASK:
{task}

Hard requirements:
- Write for the ear: conversational hook in the first sentence, open loops, curiosity gaps,
  signposting, short punchy sentences. No stage directions, no headers — pure narration.
- Total narration ≈ {target_words} words (~{minutes:g} minutes at {wpm} wpm).
- Split the narration into ABOUT {n_beats} numbered beats of roughly {words_per_beat} words each.
  Each beat = one visual moment (the image changes when the beat changes).
- For every beat, write a 'visual': a precise image-generation prompt describing ONE clear scene
  with the recurring characters, matching the art bible below, plus relevant environment, props,
  lighting and emotion. One scene per beat, strong focal action, vary camera distance/angle across
  beats. No text/words rendered inside images unless a comic symbol is essential.
- Also list up to 6 factual claims in 'fact_check' that the creator should verify before publishing.

{style_block(bible)}

Return ONLY JSON of exactly this shape:
{{
  "title": "clickable video title",
  "description": "2-3 sentence YouTube description with a soft CTA",
  "tags": ["up to 12 search tags"],
  "fact_check": ["claim worth verifying"],
  "beats": [{{"narration": "exact spoken words for this beat", "visual": "image prompt"}}]
}}"""


def generate_script(transcript: str, opts: dict, bible: dict, settings: dict) -> dict:
    client = get_client(settings)
    data = _gen_json(client, settings.get("text_model", "gemini-2.5-flash"),
                     build_script_prompt(transcript, opts, bible))
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
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", src) if len(s.strip()) > 2]
    if not sents:
        sents = [src]
    density = DENSITIES.get(opts.get("density", "normal"), DENSITIES["normal"])
    minutes = float(opts.get("target_length_min", 8))
    n_beats = min(len(sents), max(3, min(density["cap"], round(minutes * 60 / density["secs"]))))
    n_beats = min(n_beats, 12)  # mock stays small & fast
    chunks = [[] for _ in range(n_beats)]
    for i, s in enumerate(sents):
        chunks[min(n_beats - 1, i * n_beats // len(sents))].append(s)
    beats = []
    for i, chunk in enumerate(chunks):
        narration = " ".join(chunk) or sents[i % len(sents)]
        kw = " ".join(w.strip(".,!?") for w in narration.split()[:7])
        beats.append({
            "id": f"b{i + 1:03d}",
            "narration": narration,
            "visual": f"Storybook stick-character scene illustrating: {kw}… — simple 2D cartoon, muted warm palette",
        })
    return {
        "title": " ".join(src.split()[:6]).title().strip(".,!") + " — Explained",
        "description": "A mock-generated script (demo mode). Turn off Mock mode and add a Gemini key for real writing.",
        "tags": ["demo", "mock", "storybook"],
        "fact_check": ["These dates/claims came from the source transcript — verify before publishing."],
        "beats": beats,
    }


# ------------------------------------------------------------------ misc AI
def improve_visual_prompt(beat_narration: str, current: str, bible: dict, settings: dict) -> str:
    client = get_client(settings)
    prompt = f"""Rewrite this storyboard image prompt so it better matches the narration and the art bible.
Keep it a single scene, specific, visual, under 80 words. Return ONLY JSON: {{"visual": "..."}}

NARRATION: {beat_narration}
CURRENT PROMPT: {current or '(none)'}
{style_block(bible)}"""
    data = _gen_json(client, settings.get("text_model", "gemini-2.5-flash"), prompt, temperature=0.9)
    return (data.get("visual") or current).strip()


def generate_sfx_plan(beats: list[dict], timeline: list[dict], settings: dict) -> list[dict]:
    """AI decides where sound effects should land. Returns cue dicts."""
    client = get_client(settings)
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

    data = _gen_json(client, settings.get("text_model", "gemini-2.5-flash"), prompt, temperature=0.6)
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


# ------------------------------------------------------------------ tests
def test_text(settings: dict) -> str:
    client = get_client(settings)
    model = settings.get("text_model", "gemini-2.5-flash")
    data = _gen_json(client, model, 'Return ONLY JSON: {"ok": true, "model": "' + model + '"}')
    return f"OK — {model} responded"


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
