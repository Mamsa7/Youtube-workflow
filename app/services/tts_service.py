"""Voiceover synthesis: Edge TTS (primary, free), Google gTTS, and an
offline mock voice used in demo mode so the whole pipeline can be tested
without network or API keys.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

from ..config import EDGE_VOICES_FALLBACK

_voices_cache: dict = {"at": 0.0, "data": None}


# ---------------------------------------------------------------- providers
def synth_edge(text: str, voice: str, out_path: Path, rate: str = "+0%", pitch: str = "+0Hz") -> None:
    import edge_tts

    async def _go():
        tts = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
        await tts.save(str(out_path))

    asyncio.run(_go())


def synth_gtts(text: str, out_path: Path, lang: str = "en", tld: str = "com") -> None:
    from gtts import gTTS

    gTTS(text=text, lang=lang.split("-")[0] if lang else "en", tld=tld).save(str(out_path))


def synth_mock(text: str, out_path: Path) -> None:
    """Robotic mumbling placeholder — timing is what matters (≈160 wpm)."""
    sr = 22050
    words = text.split() or [""]
    rng = np.random.default_rng(abs(hash(text)) % (2**32))
    segs = []
    for w in words:
        n = int(sr * min(0.62, 0.16 + 0.045 * len(w)))
        t = np.arange(n) / sr
        f = 130 + 90 * rng.random()
        vib = 1 + 0.05 * np.sin(2 * np.pi * 5.5 * t)
        sig = 0.6 * np.sin(2 * np.pi * f * vib * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t)
        formant = 1 + 0.4 * np.sin(2 * np.pi * 2.3 * t)
        env = np.sin(np.pi * np.linspace(0, 1, n)) ** 0.7
        segs.append(sig * formant * env * 0.55)
        segs.append(np.zeros(int(sr * 0.06)))
    x = np.concatenate(segs) if segs else np.zeros(sr)
    x = np.pad(x, (int(sr * 0.08), int(sr * 0.25)))
    x = x / (np.max(np.abs(x)) or 1) * 0.7
    pcm = (x * 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


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
    synth_edge(text, settings.get("edge_voice", "en-US-GuyNeural"),
               out_path.with_suffix(".mp3"),
               rate=settings.get("edge_rate", "+0%"),
               pitch=settings.get("edge_pitch", "+0Hz"))
    return {"file": out_path.with_suffix(".mp3").name, "provider": f"edge-tts:{settings.get('edge_voice')}"}


# ---------------------------------------------------------------- utilities
def measure_duration(path: Path) -> float:
    try:
        if path.suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate())
        from mutagen.mp3 import MP3
        return float(MP3(str(path)).info.length)
    except Exception:
        return 0.0


def concat_files(files: list[Path], out_path: Path) -> Path:
    files = [f for f in files if f.exists()]
    if not files:
        raise RuntimeError("No audio files to concatenate")
    if len(files) == 1:
        shutil.copyfile(files[0], out_path)
        return out_path
    if all(f.suffix.lower() == ".wav" for f in files):
        params, frames = None, []
        for f in files:
            with wave.open(str(f), "rb") as w:
                params = w.getparams()
                frames.append(w.readframes(w.getnframes()))
        with wave.open(str(out_path), "wb") as w:
            w.setparams(params)
            for fr in frames:
                w.writeframes(fr)
        return out_path
    if shutil.which("ffmpeg"):
        list_file = out_path.with_suffix(".txt")
        list_file.write_text("".join(f"file '{f.resolve()}'\n" for f in files), encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                        "-c", "copy", str(out_path)],
                       check=True, capture_output=True)
        list_file.unlink(missing_ok=True)
        return out_path
    # Last resort: naive mp3 byte-concat (plays fine in every major editor).
    with open(out_path, "wb") as out:
        for f in files:
            out.write(f.read_bytes())
    return out_path


def list_edge_voices() -> list[dict]:
    """Live list (cached 6h); falls back to a curated static list offline."""
    import time
    if _voices_cache["data"] and time.time() - _voices_cache["at"] < 6 * 3600:
        return _voices_cache["data"]
    try:
        import edge_tts

        async def _go():
            return await edge_tts.list_voices()

        raw = asyncio.run(_go())
        voices = [{"id": v["ShortName"], "label": f'{v["ShortName"]} · {v.get("Gender", "")}'}
                  for v in raw if v.get("Locale", "").startswith("en")]
        voices.sort(key=lambda v: v["id"])
        if voices:
            _voices_cache.update(data=voices, at=time.time())
            return voices
    except Exception:
        pass
    return [{"id": vid, "label": f"{vid} · {label}"} for vid, label in EDGE_VOICES_FALLBACK]
