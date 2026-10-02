"""Timeline math: real audio timestamps, Ken Burns plan, SRT captions."""
from __future__ import annotations

MOVEMENTS = [
    ("zoom_in", "Slow zoom in"),
    ("zoom_out", "Slow zoom out"),
    ("pan_right", "Pan left → right"),
    ("pan_left", "Pan right → left"),
    ("zoom_detail", "Crop detail push"),
]


def compute_timeline(beats: list[dict], voice_beats: list[dict]) -> list[dict]:
    """voice_beats: [{'beat_id','file','duration'}] in beat order."""
    by_id = {b["id"]: b for b in beats}
    entries = []
    t = 0.0
    for i, vb in enumerate(voice_beats):
        dur = max(0.3, float(vb.get("duration") or 0.3))
        beat = by_id.get(vb["beat_id"], {})
        entries.append({
            "beat_id": vb["beat_id"],
            "index": i,
            "start": round(t, 3),
            "end": round(t + dur, 3),
            "duration": round(dur, 3),
            "text": beat.get("narration", ""),
            "movement": MOVEMENTS[i % len(MOVEMENTS)][0],
        })
        t += dur
    return entries


def srt_ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    if ms == 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _chunk_lines(text: str, max_chars: int = 38) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_chars:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def caption_cues(timeline: list[dict]) -> list[dict]:
    """[{'start','end','text'}] — ≤2 caption rows per cue, time split by words."""
    out = []
    for entry in timeline:
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        lines = _chunk_lines(text)
        cues = [lines[i:i + 2] for i in range(0, len(lines), 2)]  # ≤2 lines per cue
        weights = [max(1, sum(len(line.split()) for line in cue)) for cue in cues]
        total_w = sum(weights)
        start, end = float(entry["start"]), float(entry["end"])
        span = max(0.4, end - start)
        t = start
        for k, (cue, w) in enumerate(zip(cues, weights)):
            cue_dur = span * (w / total_w)
            cue_end = end if k == len(cues) - 1 else min(end, t + cue_dur)
            out.append({"start": round(t, 3), "end": round(cue_end, 3), "text": "\n".join(cue)})
            t = cue_end
    return out


def make_srt(timeline: list[dict]) -> str:
    blocks = []
    for idx, cue in enumerate(caption_cues(timeline), start=1):
        blocks.append(f"{idx}\n{srt_ts(cue['start'])} --> {srt_ts(cue['end'])}\n{cue['text']}\n")
    return "\n".join(blocks)


def manifest_movement(movement: str) -> dict:
    """Ken Burns parameters exporters/frontends can interpret."""
    table = {
        "zoom_in": {"start_scale": 1.0, "end_scale": 1.12, "dx": 0, "dy": 0},
        "zoom_out": {"start_scale": 1.12, "end_scale": 1.0, "dx": 0, "dy": 0},
        "pan_right": {"start_scale": 1.1, "end_scale": 1.1, "dx": 0.04, "dy": 0},
        "pan_left": {"start_scale": 1.1, "end_scale": 1.1, "dx": -0.04, "dy": 0},
        "zoom_detail": {"start_scale": 1.05, "end_scale": 1.25, "dx": 0.02, "dy": 0.02},
    }
    return table.get(movement, table["zoom_in"])
