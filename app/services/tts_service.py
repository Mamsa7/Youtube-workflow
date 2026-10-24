"""Narration synthesis.
Providers:
  - edge-tts : Microsoft Edge neural voices (free, high quality)
  - gtts     : Google Translate voice (free, robotic, last-resort)
  - elevenlabs: cloud ELEv quality voices (user's own key, free tier possible)
  - voicestudio: self-hosted OmniVoice speech service (local/LAN, CC-BY-NC default weights)

Output contract: synthesize(ss) -> (audio_bytes, content_type) where the type
is MP3 (edge/gtts/native) or derived from the output extension.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable

from app import config

# Section voices the app can auto-pick when the script tone changes mid-project
SECTION_VOICE_MAP = {
    "documentary": {"label": "Documentary (serious)", "voices": ["en-US-ChristopherNeural", "en-GB-RyanNeural"]},
    "storytime": {"label": "Storytime (friendly)", "voices": ["en-US-AriaNeural", "en-AU-NatashaNeural"]},
    "kids": {"label": "Kids (bouncy)", "voices": ["en-US-AnaNeural", "en-GB-LibbyNeural"]},
    "newsreel": {"label": "Newsreel (crisp)", "voices": ["en-US-GuyNeural", "en-IN-PrabhatNeural"]},
    "mystery": {"label": "Mystery (hushed)", "voices": ["en-US-BrianNeural", "en-GB-RyanNeural"]},
    "advert": {"label": "Advert (punchy)", "voices": ["en-US-SteffanNeural", "en-US-JennyNeural"]},
    "audiobook": {"label": "Audiobook (steady)", "voices": ["en-US-AndrewNeural", "en-GB-SoniaNeural"]},
    "podcast": {"label": "Podcast (relaxed)", "voices": ["en-US-EricNeural", "en-US-MichelleNeural"]},
}

# Narration style -> acting/performance note for engines that accept
# `instructions` (VoiceStudio SpeechRequest). Cheap wins: keeps delivery
# consistent with the script's mood instead of flat.
NARRATION_INSTRUCTIONS = {
    "documentary": "Speak with an authoritative, measured documentary-narrator tone; deliberate pacing; gravitas.",
    "storytime": "Warm, friendly storytelling voice with gentle expressiveness; sound like telling a story to a friend.",
    "kids": "Bright, playful, energetic delivery with exaggerated enthusiasm for a children's story.",
    "newsreel": "Crisp, fast-paced vintage newsreel announcer style; clipped energy and brisk clarity.",
    "mystery": "Hushed, suspenseful tone with slow pacing and dramatic pauses; conspiratorial whisper hints.",
    "advert": "Punchy, upbeat commercial voice with confident energy and emphasis on key words.",
    "audiobook": "Calm, steady, intimate audiobook narration; even pacing, subtle character inflection.",
    "podcast": "Relaxed, conversational podcast-host delivery; natural, easy-going rhythm.",
}

# Where narration voice overrides live on a ScriptSection ("voice_id" or "voice")
VOICE_FIELD_CANDIDATES = ("voice_id", "voice", "voice_name", "vs_voice", "edge_voice")


class _SpeechStopped(Exception):
    pass


# ----------------------------------------------------------------- helpers
def _ratio(settings: dict, fmt: str) -> str:
    return "9:16" if "9:16" in str(fmt or "") else "16:9"


def _is_private_or_loopback(url: str) -> bool:
    """True iff the URL clearly points at this machine or a non-routable net —
    VoiceStudio must never be exposed to the open internet, so we enforce that
    here to catch misconfig early."""
    from urllib.parse import urlparse
    import ipaddress
    host = (urlparse(url).hostname or "").strip().lower()
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False  # a DNS name — could resolve anywhere; treat as unsafe


# ------------------------------------------------------------------ Edge-TTS
def _synth_edge(text: str, settings: dict, voice: str | None = None, log: Callable[[str], None] | None = None) -> tuple[bytes, str]:
    import edge_tts  # imported lazily so gtts-only paths don't pay for it
    v = (voice or settings.get("edge_voice") or "en-US-GuyNeural").strip()
    rate = settings.get("edge_rate", "+0%")
    pitch = settings.get("edge_pitch", "+0Hz")
    async def _run() -> bytes:
        buf = bytearray()
        communicate = edge_tts.Communicate(text, v, rate=rate, pitch=pitch)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)
    try:
        data = _run_in_loop(_run())
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Edge TTS failed (voice '{v}'): {e}") from e
    if not data:
        raise RuntimeError(f"Edge TTS returned no audio with voice '{v}' — check Settings → Narration voice.")
    return data, "audio/mpeg"


def _run_in_loop(coro):
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    if loop.is_running():
        # inside FastAPI's loop — run on a fresh thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(lambda: asyncio.run(coro)).result()
    return loop.run_until_complete(coro)


# -------------------------------------------------------------------- gTTS
def _synth_gtts(text: str, settings: dict) -> tuple[bytes, str]:
    import io
    from gtts import gTTS
    mp3 = io.BytesIO()
    gTTS(text=text, lang=settings.get("gtts_lang", "en"),
         tld=settings.get("gtts_tld", "com")).write_to_fp(mp3)
    data = mp3.getvalue()
    if not data:
        raise RuntimeError("gTTS produced no audio (empty output).")
    return data, "audio/mpeg"


# ------------------------------------------------------------- ElevenLabs
def _resolve_elevenlabs_voice(name_or_id: str, key: str, log=None) -> str:
    """Voice names users type often aren't ids. Ask the account for what it has."""
    import requests
    v = (name_or_id or "").strip()
    if not v:
        raise RuntimeError("ElevenLabs voice is empty — set the voice name/id in Settings.")
    try:
        r = requests.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}, timeout=30)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Cannot reach ElevenLabs — check the internet connection. ({e})") from e
    if r.status_code == 401:
        raise RuntimeError("ElevenLabs rejected the API key (HTTP 401) — check Settings → ElevenLabs API key.")
    r.raise_for_status()
    voices = r.json().get("voices") or []
    low = v.lower()
    for voice in voices:
        if v == voice.get("voice_id"):
            return v
    for voice in voices:
        if low == (voice.get("name") or "").strip().lower():
            return voice["voice_id"]
    for voice in voices:
        if low and low in (voice.get("name") or "").strip().lower():
            return voice["voice_id"]
    names = ", ".join(vv.get("name", "?") for vv in voices[:8]) or "none"
    raise RuntimeError(
        f"ElevenLabs has no voice named '{v}' on this key. First voices on your account: {names}. "
        "Copy a name/id exactly into Settings.")


def list_elevenlabs_voices(settings: dict) -> list[dict]:
    import requests
    key = (settings.get("elevenlabs_api_key") or "").strip()
    if not key:
        raise RuntimeError("Set your ElevenLabs API key first (Settings → Narration), then press 🔍.")
    r = requests.get("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key}, timeout=30)
    if r.status_code == 401:
        raise RuntimeError("ElevenLabs rejected the API key (HTTP 401).")
    r.raise_for_status()
    return [{"id": v.get("voice_id"), "name": v.get("name"),
             "category": v.get("category", ""), "labels": v.get("labels") or {}}
            for v in (r.json().get("voices") or [])]


def _synth_elevenlabs(text: str, settings: dict, log=None) -> tuple[bytes, str]:
    import requests
    key = (settings.get("elevenlabs_api_key") or "").strip()
    if not key:
        raise RuntimeError("ElevenLabs API key missing — sign up at elevenlabs.io and add your key.")
    voice_id = _resolve_elevenlabs_voice(settings.get("elevenlabs_voice", ""), key, log)
    model = (settings.get("elevenlabs_model") or "eleven_multilingual_v2").strip()
    try:
        r = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            params={"output_format": "mp3_44100_128"},
            json={"text": text, "model_id": model,
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.72}},
            timeout=180)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Cannot reach ElevenLabs — check the internet connection. ({e})") from e
    if r.status_code == 401:
        raise RuntimeError("ElevenLabs rejected the API key (HTTP 401) — check Settings → ElevenLabs API key.")
    if r.status_code == 429:
        raise RuntimeError("ElevenLabs free quota is used up / rate-limited (HTTP 429) — wait, upgrade, or switch the narration provider back to Edge.")
    if r.status_code in (400, 422) and "model" in r.text.lower():
        raise RuntimeError(
            f"ElevenLabs rejected model '{model}'. Free accounts usually need "
            "'eleven_flash_v2_5' or 'eleven_turbo_v2_5'; update Settings → ElevenLabs model.")
    r.raise_for_status()
    return r.content, "audio/mpeg"


# ------------------------------------------------------------ VoiceStudio
def vs_headers(settings: dict) -> dict:
    """Server-side-only headers for VoiceStudio. Never raise values into UI."""
    key = (settings.get("omnivoice_api_key") or "").strip()
    pin = (settings.get("vs_share_pin") or "").strip()
    h = {}
    if key:
        h["Authorization"] = "Bearer " + key
    if pin:
        h["X-OmniVoice-Pin"] = pin
    return h


def _vs_url(settings: dict) -> str:
    u = (settings.get("voicestudio_url") or "").strip().rstrip("/")
    if not u:
        raise RuntimeError(
            "VoiceStudio URL is empty — point Settings → VoiceStudio URL at your local "
            "service (e.g. http://127.0.0.1:3900). It only runs on your own machine/LAN.")
    if not _is_private_or_loopback(u):
        raise RuntimeError(
            f"Refusing to contact VoiceStudio at '{u}' — it must be a localhost or private "
            "(192.168.x / 10.x / 172.16-31.x / Tailscale) address. This service is not safe "
            "to expose on the public internet.")
    return u


def voicestudio_discover(settings: dict) -> dict:
    """Hit VoiceStudio's discovery surface: service info, engines, voices,
    capabilities, pronunciation count. Safe to call without touching Speech."""
    import requests
    base = _vs_url(settings)
    h = vs_headers(settings)
    out = {"url": base, "reachable": False, "service": None, "version": None,
           "engines": [], "voices": [], "features": [], "pronunciation": [],
           "default_voice": "default", "docs": {}}
    # canonical discovery document
    for path in ("/.well-known/voicestudio-speech", "/.well-known/voice-studio", "/api/info", "/docs"):
        try:
            r = requests.get(base + path, headers=h, timeout=8)
            if r.ok and "application/json" in (r.headers.get("content-type") or "") or r.ok:
                try:
                    j = r.json()
                except Exception:
                    j = None
                if isinstance(j, dict):
                    out["docs"][path] = j
                    out["service"] = (j.get("service") or j.get("name") or out["service"])
                    out["version"] = (j.get("version") or out["version"])
                    feats = j.get("features") or j.get("capabilities")
                    if isinstance(feats, dict):
                        out["features"] = sorted([k for k, v in feats.items() if v])
            if r.ok:
                out["reachable"] = True
        except Exception:  # noqa: BLE001
            continue
    # engines
    for path in ("/engines", "/v1/engines", "/api/engines"):
        try:
            r = requests.get(base + path, headers=h, timeout=8)
            if r.ok:
                j = r.json()
                eng = j.get("engines") if isinstance(j, dict) else j
                if isinstance(eng, list):
                    out["engines"] = eng
                    out["reachable"] = True
                    break
        except Exception:  # noqa: BLE001
            continue
    # voices / profiles
    for path in ("/voices", "/v1/voices", "/api/voices", "/profiles"):
        try:
            r = requests.get(base + path, headers=h, timeout=8)
            if r.ok:
                j = r.json()
                vs = j.get("voices") if isinstance(j, dict) else j
                if isinstance(vs, list):
                    out["voices"] = vs
                    out["reachable"] = True
                    break
        except Exception:  # noqa: BLE001
            continue
    # pronunciation dictionary
    for path in ("/pronunciation", "/v1/pronunciation"):
        try:
            r = requests.get(base + path, headers=h, timeout=8)
            if r.ok:
                j = r.json()
                pr = j.get("entries") if isinstance(j, dict) else j
                if isinstance(pr, (list, dict)):
                    out["pronunciation"] = pr
                    out["reachable"] = True
                    break
        except Exception:  # noqa: BLE001
            continue
    return out


def _voicestudio_synthesize(text: str, settings: dict,
                            instructions: str | None = None) -> tuple[bytes, str]:
    """VoiceStudio SpeechRequest. Respects ≤4,096 chars/request; callers
    chunk longer text at sentence boundaries."""
    import requests
    base = _vs_url(settings)
    engine = (settings.get("vs_engine_id") or "omnivoice").strip() or "omnivoice"
    voice = (settings.get("vs_voice_profile_id") or "").strip() or "default"
    if len(text) > 4096:
        raise RuntimeError("VoiceStudio SpeechRequest text must be ≤ 4,096 characters.")
    payload = {
        "engine_id": engine,
        "voice": voice,
        "profile_id": voice if voice != "default" else None,
        "text": text,
        "format": "mp3",
        "speed": 1.0,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    if instructions:
        payload["instructions"] = instructions
    try:
        r = requests.post(base + "/v1/speech", json=payload,
                          headers={**vs_headers(settings), "Content-Type": "application/json"},
                          timeout=180, stream=False)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"Cannot reach VoiceStudio at {base} — is the app running? Start VoiceStudio, then retry. ({e})") from e
    if r.status_code == 429:
        ra = r.headers.get("Retry-After")
        raise RuntimeError(
            f"VoiceStudio is rate-limiting (HTTP 429); respect Retry-After={ra or '?'} — wait and retry.")
    if r.status_code == 401:
        raise RuntimeError(
            "VoiceStudio rejected authentication (HTTP 401). If the VoiceStudio PIN share "
            "feature is on, set the 6-digit PIN in Settings → VoiceStudio share PIN; "
            "otherwise leave it blank.")
    if r.status_code == 400 and "license" in r.text.lower():
        raise RuntimeError(
            "VoiceStudio refused with a license/compliance error (HTTP 400). Default OmniVoice "
            "weights are CC-BY-NC (non-commercial) — switch engine/weights or acknowledge "
            "the non-commercial terms in Settings before production use.")
    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "audio/mpeg").split(";")[0]
    return r.content, ctype


def _voicestudio_synthesize_chunked(text: str, settings: dict, log=None) -> tuple[bytes, str]:
    """Split at sentence-ish boundaries to keep each SpeechRequest ≤ 4,096."""
    chunks = _chunk_for_limit(text, 3800)
    if log and len(chunks) > 1:
        log(f"VoiceStudio: sending {len(chunks)} chunks ({len(text)} chars)…")
    instructions = None
    style = (settings.get("narration_style") or "").strip().lower()
    if style in NARRATION_INSTRUCTIONS:
        instructions = NARRATION_INSTRUCTIONS[style]
    parts: list[bytes] = []
    ctype = "audio/mpeg"
    for i, ch in enumerate(chunks):
        try:
            audio, ctype = _voicestudio_synthesize(ch, settings, instructions=instructions)
        except RuntimeError as e:
            raise RuntimeError(f"VoiceStudio chunk {i + 1}/{len(chunks)} failed: {e}") from e
        parts.append(audio)
    data = b"".join(parts)
    if not data:
        raise RuntimeError("VoiceStudio returned empty audio.")
    return data, ctype


def _chunk_for_limit(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    import re
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks, cur = [], ""
    for s in sentences:
        if not cur:
            cur = s
        elif len(cur) + 1 + len(s) <= limit:
            cur += " " + s
        else:
            chunks.append(cur)
            cur = s
        while len(cur) > limit:  # hard-split oversized sentence
            chunks.append(cur[:limit])
            cur = cur[limit:]
    if cur:
        chunks.append(cur)
    return chunks


def voicestudio_clone_voice(name: str, ref_audio_path: Path, ref_text: str,
                            settings: dict, log=None) -> dict:
    """Register a cloned voice the user *deliberately provided*; multipart
    upload stays purely server → loopback/private VoiceStudio."""
    import requests
    base = _vs_url(settings)
    if not ref_audio_path.exists():
        raise RuntimeError(f"Reference audio not found: {ref_audio_path}")
    with open(ref_audio_path, "rb") as fh:
        files = {"ref_audio": (ref_audio_path.name, fh, "application/octet-stream")}
        data = {"name": name, "ref_text": ref_text, "kind": "clone"}
        try:
            r = requests.post(base + "/v1/voices", files=files, data=data,
                              headers=vs_headers(settings), timeout=240)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Cannot reach VoiceStudio at {base} ({e})") from e
    if r.status_code in (400, 401, 403, 404, 409, 422):
        raise RuntimeError(f"VoiceStudio rejected the clone request (HTTP {r.status_code}): {r.text[:400]}")
    r.raise_for_status()
    try:
        j = r.json()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("VoiceStudio responded without JSON to the clone call.") from e
    vid = j.get("id") or j.get("profile_id") or j.get("voice_id")
    if not vid:
        raise RuntimeError("VoiceStudio clone did not return a voice id/profile_id.")
    return {"id": vid, "raw": j}


# ------------------------------------------------------------ public API
def synthesize(text: str, settings: dict, log=None) -> tuple[bytes, str]:
    provider = (settings.get("tts_provider") or "edge-tts").strip().lower()
    if provider == "edge-tts":
        return _synth_edge(text, settings, log=log)
    if provider == "gtts":
        return _synth_gtts(text, settings)
    if provider == "elevenlabs":
        return _synth_elevenlabs(text, settings, log=log)
    if provider == "voicestudio":
        # license gate must be acknowledged to use VoiceStudio for production
        if not settings.get("vs_license_ack"):
            raise RuntimeError(
                "VoiceStudio voice cloning defaults to OmniVoice weights that are CC-BY-NC "
                "(non-commercial). Tick the acknowledgement in Settings → VoiceStudio "
                "before using it for published/monetized videos.")
        try:
            return _voicestudio_synthesize_chunked(text, settings, log=log)
        except RuntimeError as e:
            if settings.get("vs_fallback_enabled"):
                fb = (settings.get("vs_fallback_provider") or "edge-tts").strip().lower()
                if log:
                    log(f"VoiceStudio failed ({e}); falling back to {fb}.")
                if fb == "gtts":
                    return _synth_gtts(text, settings)
                if fb == "elevenlabs":
                    return _synth_elevenlabs(text, settings, log=log)
                return _synth_edge(text, settings, log=log)
            raise
    raise RuntimeError(f"Unknown TTS provider '{provider}'.")


def provider_label(settings: dict) -> str:
    return {"edge-tts": "Edge TTS (Microsoft neural)",
            "gtts": "gTTS (Google Translate)",
            "elevenlabs": "ElevenLabs",
            "voicestudio": "VoiceStudio (local OmniVoice)"}.get(
        (settings.get("tts_provider") or "edge-tts"), "Edge TTS")


# ============================================================ legacy facade
# The pipeline engine (workflow.py) was written against these file-writing
# helpers — kept so mock mode + per-beat synthesis + duration measurement work
# exactly as before, with the new providers slotted in.
_voices_cache: dict = {"data": None, "at": 0.0}


def synth_edge(text: str, voice: str, out_path: Path, rate: str = "+0%", pitch: str = "+0Hz") -> None:
    import edge_tts
    async def _go():
        c = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await c.save(str(out_path))
    _run_in_loop(_go())
    if not out_path.exists() or out_path.stat().st_size < 200:
        raise RuntimeError(f"Edge TTS produced no audio with voice '{voice}'.")


def synth_gtts(text: str, out_path: Path, lang: str = "en", tld: str = "com") -> None:
    from gtts import gTTS
    gTTS(text=text, lang=lang, tld=tld).save(str(out_path))
    if not out_path.exists() or out_path.stat().st_size < 200:
        raise RuntimeError("gTTS produced no audio.")


def synth_mock(text: str, out_path: Path) -> None:
    """Offline demo narration — a soft voiced-ish tone, words-per-second-paced."""
    import math
    import struct
    import wave
    rate = 22050
    words = max(1, len(text.split()))
    dur = min(30.0, max(1.2, words * 0.38))
    n = int(rate * dur)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(n):
            t = i / rate
            # gentle syllable flutter
            f = 130 + 24 * math.sin(2 * math.pi * 3.1 * t) + 12 * math.sin(2 * math.pi * 7.7 * t)
            env = 0.5 + 0.5 * math.sin(2 * math.pi * 2.2 * t)
            fade = min(1.0, t * 6, (dur - t) * 6)
            v = int(14000 * fade * env * (0.62 * math.sin(2 * math.pi * f * t)
                                          + 0.24 * math.sin(2 * math.pi * 2 * f * t)
                                          + 0.14 * math.sin(2 * math.pi * 3 * f * t)))
            frames += struct.pack("<h", max(-32767, min(32767, v)))
        w.writeframes(bytes(frames))


def synth_beat(text: str, out_path: Path, settings: dict) -> dict:
    """Returns {'file': name, 'ext': '.mp3'|'.wav', 'provider': ...}"""
    provider = settings.get("tts_provider", "edge-tts")
    if settings.get("mock_mode"):
        synth_mock(text, out_path.with_suffix(".wav"))
        return {"file": out_path.with_suffix(".wav").name, "provider": "mock"}
    if provider == "gtts":
        synth_gtts(text, out_path.with_suffix(".mp3"),
                   lang=settings.get("gtts_lang", "en"), tld=settings.get("gtts_tld", "com"))
        return {"file": out_path.with_suffix(".mp3").name, "provider": "gtts"}
    if provider == "elevenlabs":
        data, _ = _synth_elevenlabs(text, settings)
        out_path.with_suffix(".mp3").write_bytes(data)
        return {"file": out_path.with_suffix(".mp3").name, "provider": "elevenlabs"}
    if provider == "voicestudio":
        data, ctype = synthesize(text, settings)
        ext = ".wav" if "wav" in ctype else ".mp3"
        out_path.with_suffix(ext).write_bytes(data)
        return {"file": out_path.with_suffix(ext).name, "provider": "voicestudio"}
    synth_edge(text, settings.get("edge_voice", "en-US-GuyNeural"),
               out_path.with_suffix(".mp3"),
               rate=settings.get("edge_rate", "+0%"),
               pitch=settings.get("edge_pitch", "+0Hz"))
    return {"file": out_path.with_suffix(".mp3").name,
            "provider": f"edge-tts:{settings.get('edge_voice')}"}


# ---------------------------------------------------------------- utilities
def measure_duration(path: Path) -> float:
    import wave
    try:
        if path.suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate())
        from mutagen.mp3 import MP3
        return float(MP3(str(path)).info.length)
    except Exception:
        return 0.0


def concat_files(files: list[Path], out_path: Path) -> Path:
    """Best-effort join. MP3s concatenate byte-wise safely; WAVs are mixed via
    the wave module; mismatched sets just take the first format."""
    files = [Path(f) for f in files if Path(f).exists()]
    if not files:
        raise RuntimeError("No voice files to concatenate.")
    if len(files) == 1:
        out_path.write_bytes(files[0].read_bytes())
        return out_path
    if all(f.suffix.lower() == ".wav" for f in files):
        import wave
        with wave.open(str(files[0]), "rb") as w0:
            params = w0.getparams()
            frames = [w0.readframes(w0.getnframes())]
        for f in files[1:]:
            with wave.open(str(f), "rb") as w:
                frames.append(w.readframes(w.getnframes()))
        with wave.open(str(out_path), "wb") as out:
            out.setparams(params)
            for fr in frames:
                out.writeframes(fr)
        return out_path
    out_path.write_bytes(b"".join(f.read_bytes() for f in files))
    return out_path


def list_edge_voices() -> list[dict]:
    """Live list (cached 6h); falls back to a curated static list offline."""
    import time
    if _voices_cache["data"] and time.time() - _voices_cache["at"] < 6 * 3600:
        return _voices_cache["data"]
    try:
        import asyncio as _aio
        import edge_tts

        async def _go():
            return await edge_tts.list_voices()
        raw = _run_in_loop(_go()) if False else __import__("asyncio").run(_go())
        voices = [{"id": v["ShortName"], "label": f'{v["ShortName"]} · {v.get("Gender", "")}'}
                  for v in raw if v.get("Locale", "").startswith("en")]
        voices.sort(key=lambda v: v["id"])
        if voices:
            _voices_cache.update(data=voices, at=time.time())
            return voices
    except Exception:
        pass
    return [{"id": vid, "label": f"{vid} · {label}"} for vid, label in config.EDGE_VOICES_FALLBACK]
