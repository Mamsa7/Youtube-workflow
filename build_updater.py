"""Generate update_stickstory.py — self-contained, double-click-safe updater.

Run from the repo root:  python build_updater.py
The produced updater is what users download; it:
  1. verifies it sits in the project folder (finds app/main.py),
  2. byte-writes every embedded payload,
  3. verifies each file's size against the manifest,
  4. prints clear gate lines ending in ALL DONE!.
"""
from __future__ import annotations

import base64
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

FILES = [
    "app/__init__.py",
    "app/config.py",
    "app/jobs.py",
    "app/main.py",
    "app/store.py",
    "app/services/__init__.py",
    "app/services/capcut_service.py",
    "app/services/gemini_service.py",
    "app/services/image_service.py",
    "app/services/openrouter_service.py",
    "app/services/sfx_service.py",
    "app/services/timeline_service.py",
    "app/services/tts_service.py",
    "app/services/workflow.py",
    "app/static/app.js",
    "app/static/index.html",
    "app/static/styles.css",
    "find_models.py",
    "requirements.txt",
    "tests/test_voicestudio.py",
]

STAMP = "v14 — 2026-10-24"


def main() -> None:
    out = ROOT / "update_stickstory.py"
    header = f'''"""StickStory Studio updater {STAMP}

HOW TO RUN (important — Windows opens .py files in your editor otherwise):
  1. Put this file INSIDE your StickStory project folder (next to run.py).
  2. Open a terminal there:  cmd →  cd <that folder>
  3. Run:  python update_stickstory.py
Do NOT double-click the file.
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys
from pathlib import Path

STAMP = {STAMP!r}
FILES = {{}}
'''
    entries = []
    for rel in FILES:
        src = ROOT / rel
        if not src.exists():
            print(f"MISSING: {rel}", file=sys.stderr)
            sys.exit(1)
        raw = src.read_bytes()
        entries.append((rel, len(raw), hashlib.sha256(raw).hexdigest(),
                        base64.b64encode(raw).decode("ascii")))

    body = []
    for rel, size, sha, b64 in entries:
        body.append(f"FILES[{rel!r}] = ({size}, {sha!r},\n    {b64!r})")

    footer = '''

def main() -> None:
    root = Path.cwd()
    print(f"StickStory updater {STAMP}")
    print(f"Target folder: {root}")
    if not (root / "app" / "main.py").exists():
        print("\\n!! app/main.py not found here.")
        print("   Put update_stickstory.py in the StickStory project folder")
        print("   (the one containing run.py and the app/ folder), then re-run with:")
        print("       python update_stickstory.py")
        sys.exit(2)
    wrote, verified = 0, 0
    for rel, (size, sha, b64) in FILES.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = base64.b64decode(b64.encode("ascii"))
        dest.write_bytes(payload)
        wrote += 1
        print(f"  wrote {rel} ({len(payload):,} bytes)")
        # byte verification gate
        actual = dest.read_bytes()
        if len(actual) != size or hashlib.sha256(actual).hexdigest() != sha:
            print(f"!! VERIFY FAILED on {rel} — aborting before leaving a half-update.")
            sys.exit(3)
        verified += 1
    print(f"\\n{wrote} files written, {verified}/{len(FILES)} byte-verified.")
    print("Next:  pip install -r requirements.txt   (new: gradio_client + pytest)")
    print("Then:  python run.py   and open Settings — see the VoiceStudio card.")
    print("\\nALL DONE!")


if __name__ == "__main__":
    main()
'''
    text = header.replace("FILES = {}", "FILES = {}") + "\n".join(body) + footer
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size:,} bytes, {len(FILES)} files embedded)")


if __name__ == "__main__":
    main()
