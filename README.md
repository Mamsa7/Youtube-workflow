# 🎬 StickStory Studio — YouTube Production Dashboard

Your own small **YouTube animation studio**: paste a viral/reference transcript, press
**✨ BUILD VIDEO PROJECT**, and the app drives the whole production for you —
script → voiceover (with **real audio timestamps**) → storyboard cartoon images →
sound effects → captions → a one-click **CapCut export**.

Built as a **local web app** (no hosting bill, your media stays on your PC, and it can
talk to your local CapCut drafts folder).

```text
Browser  →  http://localhost:8000
                    │
        ┌───────────▼────────────┐
        │ FastAPI dashboard      │
        │  · Gemini script engine│
        │  · Scene/image director│
        │  · Edge-TTS / gTTS     │
        │  · Procedural SFX synth│
        │  · Caption generator   │
        │  · CapCut exporter     │
        └────────────────────────┘
                    │
            data/  (projects, settings, media)
```

---

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Open **http://localhost:8000**.

1. Go to **Settings** → paste your Google AI Studio API key → **Save** →
   **Test connections** (script model / image model / TTS).
2. (Optional) Define your channel identity in the **Visual Bible** — style,
   characters, reference images. It's injected into every prompt automatically.
3. Back on **Projects**: paste a reference transcript (or leave empty for the
   built-in sample), pick format / length / visual density, press **BUILD**.
4. Review the script → **Approve** → generate **voice** → generate **images**
   (regenerate the few you dislike) → **AI sound design** → **Build SFX track** →
   **create the CapCut export**.

> **Mock mode (default when first run offline):** Settings → “Mock mode” generates
> placeholder scripts, painted preview cards and robot voice so you can rehearse the
> entire pipeline — including timeline math and CapCut exports — with **zero API cost
> and no network**. Turn it OFF for real production.

---

## The pipeline (and why this order matters)

```text
reference transcript
      │
      ▼  ① SCRIPT ENGINE (Gemini)
original, beat-by-beat narration + per-beat visual prompts
      │
      ▼  ② VOICE (Edge-TTS or gTTS)          ← BEFORE images, on purpose
one audio file per beat → REAL durations
      │
      ▼  ③ TIMELINE
scene start/end times come from measured speech, never LLM guesses
      │
      ▼  ④ STORYBOARD IMAGES (Gemini image model or Imagen)
Visual-Bible style injected automatically; approve/regenerate per scene
      │
      ▼  ⑤ SOUND EFFECTS
AI cue sheet → locally synthesized SFX → pre-mixed, timeline-aligned track
      │
      ▼  ⑥ CAPTIONS + EXPORT
SRT from real timestamps → CapCut Ready Package / CapCut Draft
```

### Script: originality first
- **Original treatment (recommended):** Gemini extracts topic, hook, structure
  and claims, then writes a fresh script — safer against reused-content strikes
  than transcript spinning, and it lists **claims to fact-check**.
- **Clone structure:** follows the reference beat-by-beat but reworded.

### Visual density (controls cost)
| Mode | New visual | Image cap | Use for |
|---|---|---|---|
| Relaxed | ~8 s | 40 | 10–20 min videos |
| Normal | ~5 s + camera moves | 60 | 5–10 min videos |
| Fast | ~3 s | 85 | commentary pacing |
| Shorts | ~2 s | 40 | vertical shorts |

Long videos generate ~40–60 strong illustrations, and the export manifest assigns
each a **Ken Burns movement** (zoom in/out, pan, detail push) so one image can cover
8–12 seconds without feeling static.

### Rate-limit resilience
Image generation saves after every scene. If Google returns a quota error, the job
**pauses instead of failing** — resume later or switch provider/model in Settings.

---

## 🔊 Sound effects

- **AI sound design** places tasteful cues per scene using the real timestamps
  (transitions, reveal pops, ambience, tension).
- Every effect is **synthesized locally with numpy** (whoosh, pop, ding, boom,
  riser, heartbeat, wind, birds, crickets, sparkle, record-scratch, applause…).
  No downloads, no licensing issues, fully editable volume per cue.
- **Upload your own samples** into the library and pick them from any cue.
- **Build SFX track** renders all cues onto one timeline-aligned `sfx_mix.wav`
  you drop under the voiceover in CapCut — done.

---

## ✂ CapCut export — two modes

**📁 CapCut Ready Package (recommended, future-proof)**
Zip with ordered images (`001_scene.png …`), full + per-scene voiceover, mixed SFX
track, `captions.srt`, your music, `timeline_manifest.json` (exact scene timings +
camera moves) and a step-by-step `README_IMPORT.txt`. Import ≈ 2 minutes and will
survive any CapCut update.

**✂ CapCut Draft (experimental)**
Writes CapCut's desktop draft format (`draft_content.json` + `draft_meta_info.json`
+ assets) with images, audio and captions pre-placed on tracks. Copy the folder into
your CapCut drafts directory (paths in the generated `README.txt`). CapCut's draft
JSON is undocumented and changes between versions — the Package export remains the
reliable fallback.

---

## Settings philosophy

No model names are hard-coded anywhere. Text model, image backend
(Gemini-native / Imagen), image model, TTS provider (Edge-TTS / gTTS), voice,
rate and pitch are all configurable, with **Test connections** buttons.
Keys are stored in `data/settings.json` locally (git-ignored) — or set
`GEMINI_API_KEY` in the environment.

> ⚠️ Paste API keys only into your own local instance. If you shared a key in a
> chat or screenshot, rotate it in Google AI Studio.

## Project layout

```text
run.py                     entry point (uvicorn on :8000)
app/
  main.py                  FastAPI routes
  config.py                settings + Visual Bible persistence
  store.py  jobs.py        project storage + background job runner
  services/
    gemini_service.py      script/storyboard/SFX prompts + JSON plumbing
    tts_service.py         Edge-TTS / gTTS / mock voice + audio utils
    image_service.py       Gemini-native & Imagen image generation + mock cards
    sfx_service.py         procedural SFX synthesis + timeline mixer
    timeline_service.py    real timestamps, Ken Burns plan, SRT captions
    capcut_service.py      Ready-Package & Draft exporters
    workflow.py            pipeline orchestration
  static/                  dashboard (vanilla JS SPA, no build step)
data/                      git-ignored: settings, bible refs, per-project media
```

## Notes
- `ffmpeg` is optional (used only for lossless mp3 concatenation; safe fallbacks
  exist for every path).
- Edge-TTS voice list is fetched live and cached; a curated fallback list works
  offline.
