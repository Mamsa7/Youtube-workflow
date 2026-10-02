"""Procedural sound-effects engine.

Every SFX is synthesized locally with numpy (no downloads, no licensing
worries). Cues are placed on a timeline and mixed into a single SFX track
that can be dropped under the voiceover in CapCut. Users can also upload
their own samples into data/sfx_library and reference them from cues.
"""
from __future__ import annotations

import math
import re
import wave
from pathlib import Path

import numpy as np

from ..config import SFX_LIBRARY_DIR

SR = 44100


# ---------------------------------------------------------------- synthesis
def _save_wav(path: Path, x: np.ndarray) -> None:
    x = np.nan_to_num(x)
    peak = np.max(np.abs(x)) or 1.0
    x = x / max(1.0, peak / 0.92)
    pcm = (x * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def _t(dur: float) -> np.ndarray:
    return np.arange(max(1, int(SR * dur))) / SR


def _env_exp(n: int, decay: float = 6.0) -> np.ndarray:
    return np.exp(-np.linspace(0, decay, n))


def _env_arc(n: int) -> np.ndarray:
    return np.sin(np.pi * np.linspace(0, 1, n)) ** 0.8


def _sweep_lp(x: np.ndarray, a_start: float, a_end: float) -> np.ndarray:
    """One-pole lowpass with a time-varying coefficient (cheap filter sweep)."""
    n = len(x)
    alphas = np.linspace(a_start, a_end, n)
    y = np.empty(n)
    acc = 0.0
    for i in range(n):
        acc = acc + alphas[i] * (x[i] - acc)
        y[i] = acc
    return y


def _noise(n: int, seed: int = 7) -> np.ndarray:
    return np.random.default_rng(seed).standard_normal(n)


def _whoosh(dur, seed=7):
    t = _t(dur)
    x = _noise(len(t), seed)
    x = _sweep_lp(x, 0.06, 0.5)
    x = _sweep_lp(x[::-1].copy(), 0.06, 0.4)[::-1]
    return x * _env_arc(len(t)) * 1.6


def _swoosh(dur, seed=8):
    t = _t(dur)
    x = _noise(len(t), seed)
    x = _sweep_lp(x, 0.45, 0.05)
    return x * _env_arc(len(t)) * 1.5


def _pop(dur, seed=0):
    t = _t(max(0.12, min(dur, 0.3)))
    f = 520 * np.exp(-t * 14) + 120
    phase = np.cumsum(2 * np.pi * f / SR)
    x = np.sin(phase) * _env_exp(len(t), 9)
    return np.pad(x, (0, max(0, int(SR * dur) - len(x))))


def _ding(dur, seed=0, base=880.0):
    t = _t(max(dur, 0.5))
    x = (np.sin(2 * np.pi * base * t)
         + 0.5 * np.sin(2 * np.pi * base * 2 * t)
         + 0.25 * np.sin(2 * np.pi * base * 3.01 * t)) * _env_exp(len(t), 5)
    return x


def _bell(dur, seed=0):
    return _ding(dur, base=660.0)


def _thud(dur, seed=0):
    t = _t(max(0.3, min(dur, 0.6)))
    f = 70 * np.exp(-t * 6) + 40
    phase = np.cumsum(2 * np.pi * f / SR)
    x = np.sin(phase) * _env_exp(len(t), 8) * 1.4
    click = _noise(int(SR * 0.012), seed) * np.exp(-np.linspace(0, 12, int(SR * 0.012)))
    x[:len(click)] += click * 0.5
    return np.pad(x, (0, max(0, int(SR * dur) - len(x))))


def _boom(dur, seed=0):
    t = _t(max(dur, 1.0))
    f = 55 * np.exp(-t * 3) + 28
    phase = np.cumsum(2 * np.pi * f / SR)
    x = np.sin(phase) * _env_exp(len(t), 3.5) * 1.6
    x += _sweep_lp(_noise(len(t), seed), 0.02, 0.005) * _env_exp(len(t), 4) * 0.6
    return x


def _riser(dur, seed=0):
    t = _t(max(dur, 0.8))
    f0, f1 = 180.0, 1500.0
    k = (f1 / f0) ** (t / t[-1])
    phase = np.cumsum(2 * np.pi * f0 * k / SR)
    env = (np.linspace(0, 1, len(t)) ** 2) * np.minimum(1, np.linspace(60, 1, len(t)))
    x = np.sin(phase) * env
    x += _noise(len(t), seed) * 0.12 * env
    x *= np.exp(-np.clip((t - t[-1] * 0.92) / (t[-1] * 0.08), 0, None) * 6)
    return x


def _click(dur, seed=0):
    n = int(SR * max(0.05, min(dur, 0.1)))
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * 2600 * t) * _env_exp(n, 18)
    x += _noise(n, seed) * _env_exp(n, 30) * 0.5
    return np.pad(x, (0, max(0, int(SR * dur) - n)))


def _camera(dur, seed=0):
    n = int(SR * 0.06)
    t = np.arange(n) / SR
    c = (np.sin(2 * np.pi * 3200 * t) + _noise(n, seed) * 0.6) * _env_exp(n, 25)
    gap = int(SR * 0.05)
    x = np.concatenate([c, np.zeros(gap), c * 0.8])
    return np.pad(x, (0, max(0, int(SR * max(dur, 0.2)) - len(x))))


def _heartbeat(dur, seed=0):
    t = _t(0.25)
    f = 65 * np.exp(-t * 5) + 42
    ph = np.cumsum(2 * np.pi * f / SR)
    th = np.sin(ph) * _env_exp(len(t), 7) * 1.4
    x = np.concatenate([th, np.zeros(int(SR * 0.08)), th * 0.7])
    total = int(SR * max(dur, 0.8))
    return np.pad(x, (0, max(0, total - len(x))))[:total]


def _wind(dur, seed=0):
    t = _t(max(dur, 2.0))
    x = _noise(len(t), seed)
    x = _sweep_lp(x, 0.015, 0.02)
    lfo = 0.75 + 0.25 * np.sin(2 * np.pi * 0.35 * t + 1.3)
    return x * lfo * 2.2 * _env_arc(len(t)) ** 0.3


def _birds(dur, seed=3):
    rng = np.random.default_rng(seed)
    total = _t(max(dur, 1.5))
    x = np.zeros(len(total))
    pos = 0.1
    while pos < total[-1] - 0.25:
        for _ in range(rng.integers(2, 5)):
            n = int(SR * 0.09)
            tt = np.arange(n) / SR
            f = rng.uniform(2800, 4200)
            ph = 2 * np.pi * (f * tt + rng.uniform(2, 5) * np.sin(2 * np.pi * rng.uniform(18, 28) * tt))
            i0 = int(pos * SR)
            if i0 + n > len(x):
                break
            x[i0:i0 + n] += np.sin(ph) * _env_arc(n) * 0.6
            pos += rng.uniform(0.06, 0.12)
        pos += rng.uniform(0.25, 0.7)
    return x


def _crickets(dur, seed=0):
    t = _t(max(dur, 2.0))
    gate = (np.sin(2 * np.pi * 24 * t) > 0.2).astype(float)
    x = np.sin(2 * np.pi * 4200 * t) * gate * 0.5
    return x * _env_arc(len(t)) ** 0.2


def _sparkle(dur, seed=0):
    notes = [1318.5, 1568.0, 2093.0, 2637.0, 3136.0]
    total = int(SR * max(dur, 0.9))
    x = np.zeros(total)
    step = int(SR * 0.09)
    for i, f in enumerate(notes):
        n = int(SR * 0.35)
        t = np.arange(n) / SR
        i0 = i * step
        if i0 + n <= total:
            x[i0:i0 + n] += np.sin(2 * np.pi * f * t) * _env_exp(n, 7) * 0.7
    return x


def _notification(dur, seed=0):
    a = _ding(0.4, base=659.25)
    b = _ding(0.6, base=987.77)
    x = np.concatenate([a, np.zeros(int(SR * 0.05)), b])
    return np.pad(x, (0, max(0, int(SR * max(dur, 1.1)) - len(x))))


def _tension(dur, seed=0):
    t = _t(max(dur, 3.0))
    x = 0.6 * np.sin(2 * np.pi * 55 * t) + 0.6 * np.sin(2 * np.pi * 55.7 * t)
    x += _sweep_lp(_noise(len(t), seed), 0.01, 0.02) * (0.4 + 0.6 * np.linspace(0, 1, len(t)))
    return x * _env_arc(len(t)) ** 0.4 * 1.2


def _paper(dur, seed=0):
    t = _t(max(dur, 0.4))
    x = _noise(len(t), seed)
    x = np.diff(x, prepend=x[0])  # crude highpass
    rustle = np.abs(np.sin(2 * np.pi * 9 * t)) ** 3
    return x * rustle * _env_exp(len(t), 4) * 1.4


def _drum(dur, seed=0):
    t = _t(max(0.4, min(dur, 0.8)))
    f = 160 * np.exp(-t * 9) + 45
    ph = np.cumsum(2 * np.pi * f / SR)
    x = np.sin(ph) * _env_exp(len(t), 6) * 1.5
    n = _noise(len(t), seed) * _env_exp(len(t), 22) * 0.4
    return np.pad(x + n, (0, max(0, int(SR * dur) - len(x))))


def _record_scratch(dur, seed=0):
    t = _t(max(dur, 0.5))
    f = 900 * np.exp(-t * 4) + 90
    wobble = 1 + 0.5 * np.sin(2 * np.pi * 13 * t)
    ph = np.cumsum(2 * np.pi * f * wobble / SR)
    x = np.sin(ph) * 0.6 + _noise(len(t), seed) * 0.25
    return x * _env_exp(len(t), 5)


def _applause(dur, seed=0):
    t = _t(max(dur, 2.0))
    x = _noise(len(t), seed)
    x = _sweep_lp(np.abs(x), 0.2, 0.25)
    env = np.minimum(1, np.linspace(0, 12, len(t))) * np.exp(-np.clip((t - t[-1] * 0.6) / (t[-1] * 0.4), 0, None) * 3)
    return x * env * 2.0


REGISTRY = {
    "whoosh":        {"fn": _whoosh, "dur": 0.8, "cat": "transition", "desc": "Air whoosh for scene transitions"},
    "swoosh":        {"fn": _swoosh, "dur": 0.7, "cat": "transition", "desc": "Downward swoosh"},
    "pop":           {"fn": _pop,    "dur": 0.15, "cat": "accent", "desc": "Cartoon pop / reveal"},
    "ding":          {"fn": _ding,   "dur": 0.9, "cat": "accent", "desc": "Idea ding / chime"},
    "bell":          {"fn": _bell,   "dur": 1.0, "cat": "accent", "desc": "Soft bell"},
    "sparkle":       {"fn": _sparkle,"dur": 0.9, "cat": "accent", "desc": "Magic sparkle arpeggio"},
    "notification":  {"fn": _notification, "dur": 1.1, "cat": "accent", "desc": "Notification two-note"},
    "click":         {"fn": _click,  "dur": 0.08, "cat": "ui", "desc": "Short click"},
    "camera":        {"fn": _camera, "dur": 0.22, "cat": "accent", "desc": "Camera shutter"},
    "thud":          {"fn": _thud,   "dur": 0.35, "cat": "impact", "desc": "Soft thud"},
    "boom":          {"fn": _boom,   "dur": 1.3, "cat": "impact", "desc": "Deep cinematic boom"},
    "drum_hit":      {"fn": _drum,   "dur": 0.5, "cat": "impact", "desc": "Taiko-style drum hit"},
    "heartbeat":     {"fn": _heartbeat, "dur": 0.8, "cat": "tension", "desc": "Two-beat heartbeat"},
    "tension":       {"fn": _tension, "dur": 4.0, "cat": "tension", "desc": "Low ominous drone"},
    "riser":         {"fn": _riser,  "dur": 1.2, "cat": "tension", "desc": "Rising build-up"},
    "record_scratch": {"fn": _record_scratch, "dur": 0.6, "cat": "comedy", "desc": "Record scratch 'wait, what?'"},
    "paper":         {"fn": _paper,  "dur": 0.45, "cat": "foley", "desc": "Paper rustle"},
    "applause":      {"fn": _applause, "dur": 2.5, "cat": "crowd", "desc": "Crowd applause swell"},
    "wind":          {"fn": _wind,   "dur": 3.0, "cat": "ambience", "desc": "Soft wind ambience"},
    "birds":         {"fn": _birds,  "dur": 3.0, "cat": "ambience", "desc": "Morning birds chirping"},
    "crickets":      {"fn": _crickets, "dur": 3.0, "cat": "ambience", "desc": "Night crickets"},
}

_WORDS = {
    name: set(re.findall(r"[a-z]+", f"{name} {v['cat']} {v['desc']}"))
    for name, v in REGISTRY.items()
}


def match_name(query: str) -> str:
    """Map a free-text cue ('transition swish', 'loud bang') to a registry name."""
    q = set(re.findall(r"[a-z]+", (query or "").lower()))
    if not q:
        return "whoosh"
    best, best_score = "whoosh", -1
    for name, words in _WORDS.items():
        score = len(q & words) * 2 + (3 if name in (query or "").lower() else 0)
        if score > best_score:
            best, best_score = name, score
    return best


def synth(name: str, path: Path, dur: float | None = None) -> Path:
    meta = REGISTRY.get(name) or REGISTRY["whoosh"]
    d = float(dur or meta["dur"])
    d = max(0.05, min(d, 8.0))
    x = meta["fn"](d)
    _save_wav(path, np.asarray(x, dtype=float))
    return path


# ---------------------------------------------------------------- mixing
def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
        ch = w.getnchannels()
    x = np.frombuffer(raw, dtype=np.int16).astype(float) / 32767.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != SR:  # naive resample (plenty for SFX)
        x = np.interp(np.linspace(0, len(x), int(len(x) * SR / sr)), np.arange(len(x)), x)
    return x, SR


def mix(cues: list[dict], total: float, out_path: Path) -> Path:
    """cues: [{'file': path, 'time': seconds, 'gain': 0..1}] → single SFX track."""
    n = max(1, int(SR * (total + 0.25)))
    canvas = np.zeros(n)
    for c in cues:
        f = Path(c["file"])
        if not f.exists() or f.suffix.lower() != ".wav":
            continue
        x, _ = _read_wav(f)
        x = x * float(c.get("gain", 0.8))
        i0 = int(float(c.get("time", 0)) * SR)
        if i0 >= n:
            continue
        seg = x[: n - i0]
        canvas[i0:i0 + len(seg)] += seg
    canvas = np.tanh(canvas * 1.05)  # soft limiter
    _save_wav(out_path, canvas)
    return out_path


def library() -> dict:
    uploaded = []
    for f in sorted(SFX_LIBRARY_DIR.iterdir()):
        if f.suffix.lower() in (".wav", ".mp3", ".ogg", ".m4a"):
            uploaded.append({"name": f.stem, "file": f.name, "kind": "uploaded"})
    return {
        "procedural": [{"name": k, "kind": "procedural", **{kk: v[kk] for kk in ("cat", "dur", "desc")}}
                       for k, v in REGISTRY.items()],
        "uploaded": uploaded,
    }
