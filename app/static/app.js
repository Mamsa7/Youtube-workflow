/* StickStory Studio — dashboard SPA */
"use strict";

/* ---------------------------------------------------------------- utils */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else if (k === "disabled") el.disabled = !!v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return el;
}

async function api(path, opts = {}) {
  const res = await fetch(path, opts.body !== undefined
    ? { method: opts.method || "POST", headers: { "Content-Type": "application/json" },
        body: typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body) }
    : { method: opts.method || "GET" });
  let data = null;
  try { data = await res.json(); } catch (e) { /* non-json */ }
  if (!res.ok || (data && data.ok === false)) {
    throw new Error((data && (data.error || data.detail)) || `HTTP ${res.status}`);
  }
  return data ? data.data ?? data : null;
}

async function apiUpload(path, file, field = "file") {
  const fd = new FormData();
  fd.append(field, file);
  const res = await fetch(path, { method: "POST", body: fd });
  const data = await res.json().catch(() => null);
  if (!res.ok || (data && data.ok === false)) {
    throw new Error((data && (data.error || data.detail)) || `HTTP ${res.status}`);
  }
  return data ? data.data ?? data : null;
}

function toast(msg, kind = "info", ms = 3800) {
  const t = h("div", { class: `toast ${kind}`, text: msg });
  $("#toast-root").append(t);
  setTimeout(() => t.remove(), ms);
}

function fmtTime(s) {
  if (s === null || s === undefined) return "--:--";
  const m = Math.floor(s / 60), sec = s - m * 60;
  return `${String(m).padStart(2, "0")}:${sec < 10 ? "0" : ""}${sec.toFixed(1)}`;
}

function fmtDur(s) { return `${(s || 0).toFixed(1)}s`; }

function badge(text, kind = "") { return h("span", { class: `badge ${kind}`, text }); }

function jobBadge(p, step) {
  const j = (p.jobs || {})[step];
  if (!j) return null;
  if (j.state === "running") return h("span", { class: "badge badge-warn", html: `<span class="spinner"></span> ${j.message || "running"}` });
  if (j.state === "error") return badge("error: " + (j.error || "").slice(0, 90), "badge-bad");
  if (j.state === "paused") return badge(j.message || "paused", "badge-info");
  if (j.state === "cancelled") return badge("stopped", "badge-warn");
  return null;
}

function stepState(p, step) {
  const j = (p.jobs || {})[step];
  if (j && j.state === "running") return "running";
  if (j && j.state === "error") return "error";
  if (j && j.state === "paused") return "paused";
  switch (step) {
    case "script": return p.script ? "done" : "idle";
    case "voice": return p.voice && p.voice.full ? "done" : "idle";
    case "images": {
      const beats = (p.script && p.script.beats) || [];
      if (!beats.length) return "idle";
      const done = beats.filter(b => (p.images[b.id] || {}).status === "done").length;
      return done >= beats.length ? "done" : (done > 0 ? "running" : "idle");
    }
    case "sfx": return p.sfx && p.sfx.mix ? "done" : "idle";
    case "captions": return p.captions ? "done" : "idle";
    case "export": return (p.exports && (p.exports.package || p.exports.draft)) ? "done" : "idle";
    default: return "idle";
  }
}

function anyRunning(p) {
  return Object.values(p.jobs || {}).some(j => j.state === "running");
}

/* ---------------------------------------------------------------- state */
const state = { settings: null, bible: null, voices: null, sfxLib: null, project: null, poll: null };

async function refreshSettings() {
  state.settings = await api("/api/settings");
  $("#mock-badge").classList.toggle("hidden", !state.settings.mock_mode);
}

/* ---------------------------------------------------------------- router */
window.addEventListener("hashchange", route);
window.addEventListener("load", route);

async function route() {
  stopPolling();
  const hash = location.hash || "#/";
  $$(".topnav a").forEach(a => a.classList.remove("active"));
  try {
    if (!state.settings) await refreshSettings();
    if (hash.startsWith("#/project/")) {
      const pid = hash.split("/")[2];
      state.project = await api(`/api/projects/${pid}`);
      viewWorkspace(state.project);
    } else if (hash.startsWith("#/bible")) {
      $('[data-nav="bible"]').classList.add("active");
      state.bible = await api("/api/bible");
      viewBible(state.bible);
    } else if (hash.startsWith("#/settings")) {
      $('[data-nav="settings"]').classList.add("active");
      viewSettings();
    } else {
      $('[data-nav="projects"]').classList.add("active");
      state.project = null;
      viewHome();
    }
  } catch (e) {
    $("#view").replaceChildren(h("div", { class: "card", html: `<h2>⚠ Something went wrong</h2><p class="muted">${e.message}</p>` }));
  }
}

/* ---------------------------------------------------------------- polling */
function stopPolling() { if (state.poll) { clearInterval(state.poll); state.poll = null; } }

function startPolling(pid) {
  stopPolling();
  state.poll = setInterval(async () => {
    try {
      const p = await api(`/api/projects/${pid}`);
      const wasRunning = state.project ? anyRunning(state.project) : false;
      state.project = p;
      const active = document.activeElement;
      const typing = active && ["TEXTAREA", "INPUT", "SELECT"].includes(active.tagName) && $("#view").contains(active);
      if (typing && anyRunning(p)) updateWorkspaceLight(p);
      else viewWorkspace(p, { keepScroll: true });
      if (!anyRunning(p) && !wasRunning) stopPolling();
    } catch (e) { stopPolling(); }
  }, 2000);
}

function goRunning(pid) { startPolling(pid); }

/* ================================================================== HOME */
async function viewHome() {
  const view = $("#view");
  view.replaceChildren();
  const projects = await api("/api/projects");

  const fmtPills = (name, opts, cur) => h("div", { class: "opt-pills" },
    opts.map(o => h("label", {}, [
      h("input", { type: "radio", name, value: o.v, checked: o.v === cur }), o.l])));

  const transcript = h("textarea", {
    rows: 7, id: "np-transcript",
    placeholder: "Paste a reference / viral transcript here… (leave empty to use the built-in sample: The Strange History of Music)"
  });
  const lengthSel = h("select", { id: "np-length" },
    [0.5, 1, 3, 5, 8, 10, 15].map(m => h("option", { value: m, text: m === 0.5 ? "30 seconds (Short)" : `${m} minutes`, ...(m === 8 ? { selected: true } : {}) })));
  const densitySel = h("select", { id: "np-density" }, [
    ["relaxed", "Relaxed · new visual ~8s (long videos)"],
    ["normal", "Normal · new visual ~5s + camera moves"],
    ["fast", "Fast · new visual ~3s"],
    ["shorts", "Shorts · new visual ~2s"],
  ].map(([v, l]) => h("option", { value: v, text: l, ...(v === "normal" ? { selected: true } : {}) })));

  const buildBtn = h("button", { class: "btn btn-primary btn-big", html: "✨ BUILD VIDEO PROJECT" });
  const hero = h("section", { class: "card" }, [
    h("div", { class: "hero-kicker", text: "① SOURCE" }),
    h("h1", { class: "hero-title", text: "Turn a viral transcript into a storyboard-ready video project" }),
    transcript,
    h("div", { class: "row", style: "margin:1rem 0 .4rem" }, [
      h("div", {}, [h("div", { class: "small muted", text: "VIDEO", style: "margin-bottom:.35rem" }),
        fmtPills("np-format", [{ v: "16:9", l: "▭ 16:9 YouTube" }, { v: "9:16", l: "▯ 9:16 Shorts" }], "16:9")]),
      h("div", {}, [h("div", { class: "small muted", text: "TARGET LENGTH", style: "margin-bottom:.35rem" }), lengthSel]),
      h("div", {}, [h("div", { class: "small muted", text: "VISUAL DENSITY", style: "margin-bottom:.35rem" }), densitySel]),
    ]),
    h("div", { class: "row", style: "margin-bottom:1.1rem" }, [
      h("div", {}, [h("div", { class: "small muted", text: "SCRIPT APPROACH", style: "margin-bottom:.35rem" }),
        fmtPills("np-mode", [{ v: "original", l: "🧭 Original treatment (recommended)" }, { v: "clone", l: "🧬 Clone structure" }], "original")]),
    ]),
    h("div", { class: "row" }, [buildBtn,
      h("span", { class: "small muted", text: "Script → voice (real timestamps) → storyboard images → SFX → captions → CapCut export" })]),
  ]);

  buildBtn.addEventListener("click", async () => {
    buildBtn.disabled = true;
    buildBtn.innerHTML = `<span class="spinner"></span> Building…`;
    try {
      const format = $('input[name="np-format"]:checked').value;
      const p = await api("/api/projects", { body: {
        source_transcript: transcript.value.trim(),
        format,
        target_length_min: parseFloat(lengthSel.value),
        density: densitySel.value,
        script_mode: $('input[name="np-mode"]:checked').value,
        autostart: true,
      }});
      location.hash = `#/project/${p.id}`;
    } catch (e) { toast(e.message, "bad"); buildBtn.disabled = false; buildBtn.innerHTML = "✨ BUILD VIDEO PROJECT"; }
  });

  const list = h("section", {}, [
    h("div", { class: "section-head" }, [h("h2", { text: "Your projects" })]),
    projects.length ? projects.map(projectCard) : h("div", { class: "card muted", text: "No projects yet — build your first one above." }),
  ]);
  view.append(hero, list);
}

function projectCard(p) {
  const j = p.jobs || {};
  const running = Object.values(j).some(x => x.state === "running");
  const imgTxt = p.beats ? `images ${p.images_done}/${p.beats}` : "no script yet";
  return h("a", { class: "project-card", href: `#/project/${p.id}` }, [
    h("div", {}, [
      h("div", { class: "title", text: p.title }),
      h("div", { class: "meta" }, [
        h("span", { text: p.format }), h("span", { text: imgTxt }),
        h("span", { text: p.script_done ? "script ✓" : "script …" }),
        h("span", { text: p.voice_done ? "voice ✓" : "voice …" }),
        h("span", { text: new Date(p.updated_at * 1000).toLocaleDateString() }),
      ]),
    ]),
    h("div", { class: "row" }, [
      running ? h("span", { class: "badge badge-warn", html: `<span class="spinner"></span> working` }) : null,
      h("button", { class: "btn btn-sm btn-bad", text: "Delete",
        onclick: async (ev) => {
          ev.preventDefault(); ev.stopPropagation();
          if (!confirm(`Delete "${p.title}" and all its media?`)) return;
          await api(`/api/projects/${p.id}`, { method: "DELETE" });
          toast("Project deleted", "warn"); route();
        } }),
    ]),
  ]);
}

/* ============================================================== WORKSPACE */
function stepper(p) {
  const steps = [["script", "SCRIPT"], ["voice", "VOICE"], ["images", "STORYBOARD"], ["sfx", "SFX"], ["captions", "CAPTIONS"], ["export", "CAPCUT"]];
  return h("div", { class: "stepper" }, steps.map(([k, label]) => {
    const st = stepState(p, k);
    let extra = "";
    if (k === "images" && p.script) {
      const beats = p.script.beats || [];
      const done = beats.filter(b => (p.images[b.id] || {}).status === "done").length;
      extra = ` ${done}/${beats.length}`;
    }
    return h("div", { class: `step-pill ${st}`, "data-step": k }, [
      h("span", { class: "dot" }), h("span", { text: label + extra }),
      st === "done" ? h("span", { text: "✓" }) : null]);
  }));
}

function progressStrip(p) {
  const rows = [];
  const defs = [["script", "Script"], ["voice", "Voice"], ["images", "Images"], ["sfx", "Sound FX"]];
  let active = false;
  for (const [k, label] of defs) {
    const j = (p.jobs || {})[k];
    if (!j || j.state === "idle") continue;
    const total = j.total || 0, done = j.done || 0;
    const pct = j.state === "done" ? 100 : (total ? Math.round(100 * done / total) : 50);
    if (j.state === "running" || j.state === "paused") active = true;
    let right;
    if (k === "images" && p.script) {
      const beats = p.script.beats || [];
      const real = beats.filter(b => (p.images[b.id] || {}).status === "done").length;
      right = `${real}/${beats.length}`;
    } else right = total ? `${done}/${total}` : "";
    rows.push(h("div", { class: "pbar-row" }, [
      h("span", { class: "muted", text: label }),
      h("div", { class: `pbar ${j.state === "done" ? "good" : ""}` }, [h("i", { style: `width:${pct}%` })]),
      h("span", { class: "muted nowrap", text: j.state === "error" ? "error" : right }),
    ]));
  }
  if (!rows.length) return null;
  return h("div", { class: "card" }, [
    h("div", { class: "section-head" }, [
      h("h3", { text: active ? "Generation queue" : "Last run" }),
      active ? h("button", { class: "btn btn-sm btn-bad", text: "⏸ Stop generation",
        onclick: async () => { await api(`/api/projects/${p.id}/jobs/cancel`, { body: {} }); toast("Stop requested", "warn"); } }) : null,
    ]),
    h("div", { class: "progress-strip" }, rows),
  ]);
}

function viewWorkspace(p) {
  const view = $("#view");
  const scrollY = window.scrollY;
  view.replaceChildren();

  /* header */
  const head = h("div", { class: "row", style: "justify-content:space-between" }, [
    h("div", {}, [
      h("a", { href: "#/", class: "small", text: "← Projects" }),
      h("h1", { style: "margin:.2rem 0 0;font-size:1.4rem", text: p.title }),
      h("div", { class: "small muted", text: `${p.format} · target ${p.target_length_min} min · ${p.density} · ${p.script_mode} mode` }),
    ]),
    (p.jobs && Object.values(p.jobs).some(j => j.state === "running"))
      ? h("button", { class: "btn btn-bad", text: "⏸ Stop all",
          onclick: () => api(`/api/projects/${p.id}/jobs/cancel`, { body: {} }).then(() => toast("Stop requested", "warn")) })
      : null,
  ]);

  view.append(head, stepper(p));
  const prog = progressStrip(p);
  if (prog) view.append(prog);
  view.append(sectionScript(p), sectionVoice(p), sectionStoryboard(p), sectionSfx(p), sectionExport(p));
  if (anyRunning(p)) startPolling(p.id);
  window.scrollTo(0, scrollY);
}

/* light refresh: only statuses/images while user types */
function updateWorkspaceLight(p) {
  for (const [k] of [["script"], ["voice"], ["images"], ["sfx"], ["captions"], ["export"]]) {
    const pill = $(`.step-pill[data-step="${k}"]`);
    if (pill) pill.className = `step-pill ${stepState(p, k)}`;
  }
  $$(".scene-card[data-beat]").forEach(card => {
    const bid = card.getAttribute("data-beat");
    const rec = (p.images || {})[bid] || {};
    const cur = card.getAttribute("data-status");
    const nxt = rec.status || "none";
    if (cur !== nxt) {
      const fresh = sceneCard(p, (p.script.beats || []).find(b => b.id === bid));
      if (fresh) card.replaceWith(fresh);
    }
  });
}

/* ---------------------------------------------------------------- SCRIPT */
function sectionScript(p) {
  const sec = h("section", { class: "card", id: "sec-script" });
  const st = stepState(p, "script");
  const heads = [h("h2", { text: "② SCRIPT" }), jobBadge(p, "script"), st === "done"
    ? (p.script_approved ? badge("✓ approved", "badge-good") : badge("review needed", "badge-warn"))
    : badge("not started")].filter(Boolean);
  sec.append(h("div", { class: "section-head" }, [
    h("div", { class: "row", style: "gap:.6rem" }, heads),
    h("button", { class: "btn", html: p.script ? "🔁 Regenerate script" : "✨ Generate script",
      onclick: () => runJob(p, "script", `/api/projects/${p.id}/script/generate`) }),
  ]));
  if (!p.script) {
    sec.append(h("p", { class: "muted", text: "Gemini will turn the source transcript into an original, beat-by-beat script matched to your Visual Bible." }));
    return sec;
  }
  const s = p.script;
  sec.append(
    h("div", { class: "row", style: "margin-bottom:.6rem" }, [
      h("h3", { style: "margin:0;font-size:1.05rem;text-transform:none;letter-spacing:0;color:var(--text)", text: s.title || p.title }),
      h("span", { class: "muted small", text: `${(s.beats || []).length} beats · ~${wordCount(s)} words · ~${Math.round(wordCount(s) / 165)} min` }),
    ]),
    s.description ? h("p", { class: "muted small", text: s.description }) : null,
    (s.tags || []).length ? h("div", { class: "row", style: "gap:.35rem;margin-bottom:.5rem" },
      s.tags.slice(0, 10).map(t => badge("#" + t))) : null,
    (s.fact_check || []).length ? h("details", { class: "fold", style: "margin-bottom:.7rem" }, [
      h("summary", { text: `⚠ Fact-check before publishing (${s.fact_check.length})` }),
      h("ul", {}, s.fact_check.map(f => h("li", { text: f })))]) : null,
  );
  const beatsList = h("div", { style: "display:grid;gap:.6rem" },
    (s.beats || []).map((b, i) => scriptBeatRow(p, b, i)));
  sec.append(beatsList);
  sec.append(h("div", { class: "row", style: "margin-top:.9rem" }, [
    h("button", {
      class: `btn ${p.script_approved ? "" : "btn-good"}`,
      text: p.script_approved ? "✓ Script approved (click to un-approve)" : "✓ Approve script — continue to voice",
      onclick: async () => {
        await api(`/api/projects/${p.id}/script/approve`, { body: { approved: !p.script_approved }});
        state.project = await api(`/api/projects/${p.id}`); viewWorkspace(state.project);
      } }),
  ]));
  return sec;
}

function wordCount(s) { return (s.beats || []).reduce((n, b) => n + (b.narration || "").split(/\s+/).length, 0); }

function scriptBeatRow(p, b, i) {
  const nar = h("textarea", { rows: 2, class: "mono", style: "font-size:.85rem", text: b.narration });
  const vis = h("textarea", { rows: 2, class: "mono", style: "font-size:.82rem", text: b.visual });
  nar.addEventListener("change", () => saveBeat(p, b.id, { narration: nar.value }));
  vis.addEventListener("change", () => saveBeat(p, b.id, { visual: vis.value }));
  return h("div", { class: "char-chip", style: "align-items:stretch" }, [
    h("div", { class: "muted small mono", style: "min-width:44px;padding-top:.5rem", text: `#${i + 1}` }),
    h("div", { style: "flex:1;display:grid;gap:.4rem" }, [
      h("div", { class: "small muted", text: "VOICEOVER" }), nar,
      h("div", { class: "small muted", text: "VISUAL PROMPT" }), vis,
      h("div", { class: "row" }, [
        h("button", { class: "btn btn-sm", html: "✨ AI rewrite prompt",
          onclick: async (ev) => {
            ev.target.disabled = true;
            try { const nb = await api(`/api/projects/${p.id}/beats/${b.id}/visual_auto`); vis.value = nb.visual; toast("Prompt rewritten", "good"); }
            catch (e) { toast(e.message, "bad"); }
            ev.target.disabled = false;
          } }),
      ]),
    ]),
  ]);
}

async function saveBeat(p, bid, patch) {
  try { await api(`/api/projects/${p.id}/beats/${bid}`, { method: "PUT", body: patch }); toast("Saved", "good", 1500); }
  catch (e) { toast(e.message, "bad"); }
}

/* ---------------------------------------------------------------- VOICE */
function sectionVoice(p) {
  const sec = h("section", { class: "card", id: "sec-voice" });
  const st = stepState(p, "voice");
  sec.append(h("div", { class: "section-head" }, [
    h("div", { class: "row", style: "gap:.6rem" }, [
      h("h2", { text: "③ VOICE & TIMELINE" }), jobBadge(p, "voice"),
      st === "done" ? badge("✓ voice ready", "badge-good") : badge("not started")].filter(Boolean)),
    h("div", { class: "row" }, [
      h("span", { class: "small muted", text: `${state.settings.tts_provider === "gtts" ? "Google TTS" : "Edge TTS"} · ${state.settings.tts_provider === "gtts" ? state.settings.gtts_lang : state.settings.edge_voice}` }),
      h("a", { class: "small", href: "#/settings", text: "change" }),
      h("button", { class: "btn btn-primary", disabled: !p.script,
        html: p.voice && p.voice.full ? "🔁 Regenerate voice" : "🎙 Generate voiceover",
        onclick: () => runJob(p, "voice", `/api/projects/${p.id}/voice/generate`) }),
    ]),
  ]));
  if (!p.script) { sec.append(h("p", { class: "muted", text: "Generate the script first." })); return sec; }
  if (!p.voice || !p.voice.beats || !p.voice.beats.length) {
    sec.append(h("p", { class: "muted", text: "The voiceover is generated per scene so your storyboard timing comes from REAL audio timestamps — never LLM guesses." }));
    return sec;
  }
  const total = p.timeline.length ? p.timeline[p.timeline.length - 1].end : 0;
  sec.append(h("div", { class: "row", style: "margin-bottom:.7rem" }, [
    badge(`total runtime ${fmtTime(total)}`, "badge-info"),
    badge(`${p.voice.beats.length} scenes`, ""),
    p.voice.voice_name ? badge(p.voice.voice_name, "") : null,
  ]));
  if (p.voice.full) {
    sec.append(h("div", { class: "fold", style: "margin-bottom:.8rem" }, [
      h("div", { class: "small muted", style: "margin-bottom:.35rem", text: "FULL VOICEOVER" }),
      h("audio", { controls: true, src: `/api/projects/${p.id}/media/${p.voice.full}?t=${Date.now()}` }),
    ]));
  }
  const det = h("details", { class: "fold" }, [
    h("summary", { text: `Per-scene audio & real timestamps (${p.voice.beats.length})` }),
    h("div", { style: "margin-top:.6rem" }, p.voice.beats.map((vb, i) =>
      h("div", { class: "player-row" }, [
        h("span", { class: "mono muted nowrap", text: `#${i + 1} · ${fmtTime(p.timeline[i] ? p.timeline[i].start : 0)}` }),
        h("audio", { controls: true, src: `/api/projects/${p.id}/media/${vb.file}?t=${Date.now()}` }),
        h("span", { class: "mono muted", text: fmtDur(vb.duration) }),
      ]))),
  ]);
  sec.append(det);
  return sec;
}

/* ------------------------------------------------------------ STORYBOARD */
function sectionStoryboard(p) {
  const sec = h("section", { class: "card", id: "sec-storyboard" });
  const beats = (p.script && p.script.beats) || [];
  const done = beats.filter(b => (p.images[b.id] || {}).status === "done").length;
  const approved = beats.filter(b => (p.images[b.id] || {}).approved).length;
  sec.append(h("div", { class: "section-head" }, [
    h("div", { class: "row", style: "gap:.6rem" }, [
      h("h2", { text: "④ STORYBOARD & IMAGES" }), jobBadge(p, "images"),
      beats.length ? badge(`${done}/${beats.length} painted${approved ? ` · ${approved} approved` : ""}`, done === beats.length ? "badge-good" : "badge-warn") : badge("not started"),
    ].filter(Boolean)),
    h("div", { class: "row" }, [
      !p.timeline.length && beats.length ? badge("tip: generate voice first for exact sync", "badge-info") : null,
      h("button", { class: "btn btn-primary", disabled: !beats.length,
        html: done ? "🖼 Generate missing images" : "🖼 Generate all images",
        onclick: () => runJob(p, "images", `/api/projects/${p.id}/images/generate`, { body: {} }) }),
    ]),
  ]));
  if (!beats.length) { sec.append(h("p", { class: "muted", text: "Generate the script first — every beat becomes a frame here." })); return sec; }
  const grid = h("div", { class: "scene-grid" }, beats.map(b => sceneCard(p, b)));
  sec.append(grid);
  return sec;
}

function sceneCard(p, b) {
  const rec = (p.images || {})[b.id] || {};
  const status = rec.status || "none";
  const tl = (p.timeline || []).find(t => t.beat_id === b.id);
  const idx = parseInt(b.id.slice(1), 10);

  const media = h("div", { class: "scene-media" });
  if (status === "done" && rec.file) {
    media.append(h("img", { src: `/api/projects/${p.id}/media/${rec.file}?v=${rec.updated || 0}`, loading: "lazy" }));
  } else {
    media.append(h("div", { class: "placeholder", text: status === "error" ? "⚠" : "🖼" }));
  }
  if (status === "generating") media.append(h("div", { class: "scene-overlay", html: `<span class="spinner"></span>&nbsp;painting…` }));
  if (rec.approved) media.append(h("div", { class: "scene-approved-flag", text: "✓ APPROVED" }));
  if (tl) media.append(h("div", { class: "scene-time", text: `${fmtTime(tl.start)} → ${fmtTime(tl.end)}` }));

  const prompt = h("textarea", { class: "scene-prompt", rows: 3, text: b.visual });
  const saveBtn = h("button", { class: "btn btn-sm", text: "💾 Save prompt", onclick: () => saveBeat(p, b.id, { visual: prompt.value }) });

  const regenBtn = h("button", { class: "btn btn-sm", html: "🔁 Regenerate", disabled: p.jobs.images && p.jobs.images.state === "running",
    onclick: () => runJob(p, "images", `/api/projects/${p.id}/images/${b.id}/regenerate`) });

  const upInput = h("input", { type: "file", accept: "image/*", class: "hidden" });
  upInput.addEventListener("change", async () => {
    if (!upInput.files.length) return;
    try { await apiUpload(`/api/projects/${p.id}/images/${b.id}/upload`, upInput.files[0]); toast("Uploaded", "good"); }
    catch (e) { toast(e.message, "bad"); }
    state.project = await api(`/api/projects/${p.id}`); viewWorkspace(state.project);
  });
  const upBtn = h("button", { class: "btn btn-sm", text: "⬆ Upload", onclick: () => upInput.click() });

  const approveBtn = h("button", {
    class: `btn btn-sm ${rec.approved ? "" : "btn-good"}`,
    text: rec.approved ? "✓ Approved" : "Approve",
    disabled: status !== "done",
    onclick: async () => {
      await api(`/api/projects/${p.id}/images/${b.id}/approve`, { body: { approved: !rec.approved }});
      state.project = await api(`/api/projects/${p.id}`);
      const card = $(`.scene-card[data-beat="${b.id}"]`);
      if (card) card.replaceWith(sceneCard(state.project, b));
    } });

  const body = h("div", { class: "scene-body" }, [
    h("div", { class: "row", style: "justify-content:space-between" }, [
      h("strong", { text: `SCENE ${String(idx).padStart(2, "0")}` }),
      rec.source && rec.source !== "mock" ? badge(rec.source, rec.source === "upload" ? "badge-info" : "") : null,
      tl ? badge(tl.movement.replaceAll("_", " "), "badge-info") : null,
    ].filter(Boolean)),
    h("div", { class: "scene-vo", text: `“${b.narration}”` }),
    status === "error" && rec.error ? h("div", { class: "error-box", text: "⚠ " + rec.error }) : null,
    h("div", { class: "small muted", text: "VISUAL" }), prompt,
    h("div", { class: "scene-actions" }, [saveBtn, regenBtn, upBtn, approveBtn, upInput]),
  ]);

  return h("div", {
    class: `scene-card ${p.format === "9:16" ? "fmt-s916" : ""}`,
    "data-beat": b.id, "data-status": status,
  }, [media, body]);
}

/* ------------------------------------------------------------------ SFX */
async function ensureSfxLib() {
  if (!state.sfxLib) state.sfxLib = await api("/api/sfx/library");
  return state.sfxLib;
}

function sectionSfx(p) {
  const sec = h("section", { class: "card", id: "sec-sfx" });
  const plan = (p.sfx && p.sfx.plan) || [];
  sec.append(h("div", { class: "section-head" }, [
    h("div", { class: "row", style: "gap:.6rem" }, [
      h("h2", { text: "⑤ SOUND EFFECTS" }), jobBadge(p, "sfx"),
      plan.length ? badge(`${plan.length} cues`, "badge-info") : badge("no cues"),
      p.sfx && p.sfx.mix ? badge("✓ mix built", "badge-good") : null,
    ].filter(Boolean)),
    h("div", { class: "row" }, [
      h("button", { class: "btn", disabled: !p.script, html: "🪄 AI sound design",
        onclick: () => runJob(p, "sfx", `/api/projects/${p.id}/sfx/plan`) }),
      h("button", { class: "btn", html: "＋ Add cue", disabled: !p.script,
        onclick: async () => {
          await api(`/api/projects/${p.id}/sfx/cues`, { body: { beat: 1, offset: 0, name: "whoosh", gain: 0.8 } });
          state.project = await api(`/api/projects/${p.id}`); viewWorkspace(state.project);
        } }),
      h("button", { class: "btn btn-primary", disabled: !plan.length, html: "🔨 Build SFX track",
        onclick: async () => {
          try { const r = await api(`/api/projects/${p.id}/sfx/build`, { body: {} });
            toast(`SFX track built — ${r.cues} cues`, "good");
            state.project = await api(`/api/projects/${p.id}`); viewWorkspace(state.project);
          } catch (e) { toast(e.message, "bad"); }
        } }),
    ]),
  ]));
  sec.append(h("p", { class: "small muted", style: "margin-top:-.3rem",
    text: "Effects are synthesized locally (no downloads) or taken from your uploaded library, then pre-mixed onto one timeline-aligned track for CapCut." }));

  if (p.sfx && p.sfx.mix) {
    sec.append(h("div", { class: "fold", style: "margin-bottom:.8rem" }, [
      h("div", { class: "row", style: "justify-content:space-between" }, [
        h("span", { class: "small muted", text: "MIXED SFX TRACK (drop under the voiceover in CapCut)" }),
        h("a", { class: "btn btn-sm", href: `/api/projects/${p.id}/media/${p.sfx.mix}?dl=1`, download: "sfx_mix.wav", text: "⬇ download" }),
      ]),
      h("audio", { controls: true, src: `/api/projects/${p.id}/media/${p.sfx.mix}?t=${Date.now()}` }),
    ]));
  }

  if (plan.length) {
    const tbl = h("table", { class: "tbl" }, [
      h("thead", {}, h("tr", {}, ["#", "Scene", "At", "Sound", "Gain", "▶", ""].map(t => h("th", { text: t })))),
      h("tbody", {}, plan.map((cue, i) => sfxCueRow(p, cue, i))),
    ]);
    sec.append(tbl);
  }

  const upLib = h("div", { class: "row", style: "margin-top:.8rem" }, [
    h("label", { class: "btn btn-sm", text: "⬆ Upload your own SFX (.wav/.mp3)…" }, [
      h("input", { type: "file", accept: "audio/*", class: "hidden",
        onchange: async (ev) => {
          if (!ev.target.files.length) return;
          await apiUpload("/api/sfx/library", ev.target.files[0]);
          state.sfxLib = null; toast("SFX added to library", "good");
        } })]),
    h("span", { class: "small muted", text: "…then pick it in any cue's Sound column" }),
  ]);
  sec.append(upLib);
  return sec;
}

function sfxCueRow(p, cue, i) {
  const beats = (p.script && p.script.beats) || [];
  const tl = (p.timeline || [])[cue.beat - 1];
  const at = cue.time !== undefined ? cue.time : (tl ? tl.start + (cue.offset || 0) : null);

  const sceneSel = h("select", {}, beats.map((b, bi) =>
    h("option", { value: bi + 1, text: `Scene ${bi + 1}`, ...(bi + 1 === cue.beat ? { selected: true } : {}) })));
  sceneSel.addEventListener("change", () => editCue(p, i, { beat: parseInt(sceneSel.value) }));

  const offInput = h("input", { type: "number", min: 0, step: 0.1, value: cue.offset ?? 0, style: "width:70px" });
  offInput.addEventListener("change", () => editCue(p, i, { offset: parseFloat(offInput.value) || 0 }));

  const soundSel = h("select", { style: "min-width:190px" });
  ensureSfxLib().then(lib => {
    const g1 = h("optgroup", { label: "Procedural (synthesized)" });
    lib.procedural.forEach(s => g1.append(h("option", {
      value: "pro:" + s.name, text: `${s.name} — ${s.desc}`,
      ...(cue.source !== "uploaded" && sfx_serviceName(cue) === s.name ? { selected: true } : {}) })));
    const g2 = h("optgroup", { label: "My uploads" });
    lib.uploaded.forEach(s => g2.append(h("option", {
      value: "up:" + s.file, text: "📁 " + s.name,
      ...(cue.source === "uploaded" && cue.file === s.file ? { selected: true } : {}) })));
    soundSel.append(g1, g2);
    const cur = cue.source === "uploaded" ? "up:" + (cue.file || "") : "pro:" + sfx_serviceName(cue);
    if ([...soundSel.options].some(o => o.value === cur)) soundSel.value = cur;
  });
  soundSel.addEventListener("change", () => {
    const v = soundSel.value;
    editCue(p, i, v.startsWith("up:")
      ? { source: "uploaded", file: v.slice(3) }
      : { source: "procedural", name: v.slice(4), file: null });
  });

  const gainInput = h("input", { type: "number", min: 0.1, max: 1, step: 0.05, value: cue.gain ?? 0.8, style: "width:64px" });
  gainInput.addEventListener("change", () => editCue(p, i, { gain: parseFloat(gainInput.value) || 0.8 }));

  const playBtn = h("button", { class: "btn btn-sm", text: "▶",
    onclick: () => {
      const url = cue.source === "uploaded" && cue.file
        ? `/api/sfx/preview?kind=uploaded&file=${encodeURIComponent(cue.file)}`
        : `/api/sfx/preview?name=${encodeURIComponent(sfx_serviceName(cue))}`;
      new Audio(url).play().catch(() => toast("Preview unavailable", "bad"));
    } });
  const delBtn = h("button", { class: "btn btn-sm btn-bad", text: "✕",
    onclick: async () => {
      await api(`/api/projects/${p.id}/sfx/cues/${i}`, { method: "DELETE" });
      state.project = await api(`/api/projects/${p.id}`); viewWorkspace(state.project);
    } });

  return h("tr", {}, [
    h("td", { class: "muted", text: String(i + 1) }),
    h("td", {}, sceneSel),
    h("td", { class: "nowrap" }, [offInput, h("span", { class: "small muted", text: at !== null ? ` → ${fmtTime(at)}` : "" })]),
    h("td", {}, soundSel),
    h("td", {}, gainInput),
    h("td", {}, playBtn),
    h("td", {}, delBtn),
  ]);
}

function sfx_serviceName(cue) {
  if (cue.source === "uploaded") return cue.file || "";
  return cue.resolved || (cue.name || "whoosh");
}

async function editCue(p, i, patch) {
  try { await api(`/api/projects/${p.id}/sfx/cues/${i}`, { method: "PUT", body: patch }); }
  catch (e) { toast(e.message, "bad"); }
}

/* ---------------------------------------------------------------- EXPORT */
function sectionExport(p) {
  const sec = h("section", { class: "card", id: "sec-export" });
  const beats = (p.script && p.script.beats) || [];
  const imgs = beats.filter(b => (p.images[b.id] || {}).status === "done").length;
  const ready = p.timeline.length && beats.length && imgs === beats.length && p.voice && p.voice.full;
  const exp = p.exports || {};

  sec.append(h("div", { class: "section-head" }, [
    h("div", { class: "row", style: "gap:.6rem" }, [
      h("h2", { text: "⑥ CAPTIONS & EXPORT" }),
      ready ? badge("ready to export", "badge-good") : badge("finish the steps above first", "badge-warn"),
    ]),
    h("div", { class: "row" }, [
      h("button", { class: "btn", disabled: !p.timeline.length, html: "💬 Generate captions (.srt)",
        onclick: async () => {
          try { await api(`/api/projects/${p.id}/captions/generate`, { body: {} });
            toast("Captions generated", "good");
            state.project = await api(`/api/projects/${p.id}`); viewWorkspace(state.project);
          } catch (e) { toast(e.message, "bad"); }
        } }),
      p.captions ? h("a", { class: "btn btn-sm", href: `/api/projects/${p.id}/media/${p.captions}?dl=1`, download: "captions.srt", text: "⬇ captions.srt" }) : null,
    ].filter(Boolean)),
  ]));

  /* music */
  const musicRow = h("div", { class: "row", style: "margin-bottom:1rem" }, [
    h("span", { class: "small muted", text: "Background music (optional):" }),
    p.music ? badge("🎵 " + p.music, "badge-good") : badge("none"),
    h("label", { class: "btn btn-sm", text: p.music ? "Replace…" : "Upload…" }, [
      h("input", { type: "file", accept: "audio/*", class: "hidden",
        onchange: async (ev) => {
          if (!ev.target.files.length) return;
          await apiUpload(`/api/projects/${p.id}/music`, ev.target.files[0]);
          state.project = await api(`/api/projects/${p.id}`); viewWorkspace(state.project);
        } })]),
  ]);
  sec.append(musicRow);

  const pkgCard = h("div", { class: "export-card recommended" }, [
    h("h3", { text: "📁 CapCut Ready Package — recommended" }),
    h("ul", {}, [
      h("li", { text: "Ordered scene images (001, 002, …)" }),
      h("li", { text: "Voiceover (full + per-scene)" }),
      h("li", { text: "Pre-mixed SFX track, timeline-aligned" }),
      h("li", { text: "SRT captions + timeline manifest with exact timings" }),
      p.music ? h("li", { text: "Background music" }) : null,
    ].filter(Boolean)),
    h("div", { class: "row" }, [
      h("button", { class: "btn btn-primary", disabled: !ready, html: "📦 Create package",
        onclick: () => doExport(p, "package") }),
      exp.package ? h("a", { class: "btn btn-good", href: `/api/projects/${p.id}/media/${exp.package.file}?dl=1`, download: true, text: "⬇ Download zip" }) : null,
    ]),
    h("p", { class: "small muted", text: "Reliable forever: import takes ~2 minutes and survives any CapCut update." }),
  ]);

  const draftCard = h("div", { class: "export-card" }, [
    h("h3", { text: "✂ CapCut Draft — experimental" }),
    h("ul", {}, [
      h("li", { text: "Writes CapCut's desktop draft format directly" }),
      h("li", { text: "Images, voice, SFX track & captions pre-placed on tracks" }),
      h("li", { text: "Copy the folder into your CapCut drafts directory" }),
      h("li", { text: "⚠ Draft JSON is undocumented — may break on CapCut updates" }),
    ]),
    h("div", { class: "row" }, [
      h("button", { class: "btn", disabled: !ready, html: "✂ Build draft",
        onclick: () => doExport(p, "draft") }),
      exp.draft ? h("a", { class: "btn", href: `/api/projects/${p.id}/media/${exp.draft.file}?dl=1`, download: true, text: "⬇ Download zip" }) : null,
    ]),
    exp.draft && exp.draft.draft_folder ? h("p", { class: "small mono muted", text: "Draft folder: " + exp.draft.draft_folder }) : null,
  ]);

  sec.append(h("div", { class: "export-cards" }, [pkgCard, draftCard]));
  return sec;
}

async function doExport(p, kind) {
  try {
    toast(kind === "package" ? "Assembling package…" : "Writing CapCut draft…", "info", 2000);
    await api(`/api/projects/${p.id}/export/${kind}`, { body: {} });
    toast("Export ready ⬇", "good");
    state.project = await api(`/api/projects/${p.id}`);
    viewWorkspace(state.project);
    setTimeout(() => { const s = $("#sec-export"); if (s) s.scrollIntoView({ behavior: "smooth" }); }, 50);
  } catch (e) { toast(e.message, "bad"); }
}

/* ------------------------------------------------------------------ jobs */
async function runJob(p, step, url, opts = {}) {
  try {
    await api(url, { method: "POST", body: opts.body ?? {} });
    goRunning(p.id);
    state.project = await api(`/api/projects/${p.id}`);
    viewWorkspace(state.project);
  } catch (e) { toast(e.message, "bad"); }
}

/* ================================================================== BIBLE */
function viewBible(b) {
  const view = $("#view");
  view.replaceChildren();
  view.append(h("h1", { text: "Visual Bible" }),
    h("p", { class: "muted", text: "Your channel's permanent visual identity — automatically injected into every generated image and script prompt. Define it once, never prompt it again." }));

  const fields = [
    ["style", "STYLE"], ["linework", "LINEWORK"], ["faces", "FACES"],
    ["color", "COLOR & LIGHT"], ["background", "BACKGROUNDS"], ["composition", "COMPOSITION"],
    ["negative", "NEGATIVE PROMPT (things to avoid)"],
  ];
  const inputs = {};
  const styleCard = h("section", { class: "card" }, [
    h("h2", { text: "Art direction" }),
    fields.map(([k, label]) => {
      inputs[k] = h("textarea", { rows: k === "negative" ? 2 : 2, text: b[k] || "" });
      return h("label", { class: "field" }, [h("span", { text: label }), inputs[k]]);
    }),
    h("button", { class: "btn btn-primary", text: "💾 Save art direction",
      onclick: async () => {
        const patch = {};
        for (const [k] of fields) patch[k] = inputs[k].value;
        state.bible = await api("/api/bible", { method: "PUT", body: patch });
        toast("Visual Bible saved", "good");
      } }),
  ]);

  /* characters */
  const charCard = h("section", { class: "card" }, [
    h("h2", { text: "Recurring characters" }),
    h("p", { class: "small muted", text: "Character descriptions are injected into every image prompt so your cast stays consistent. Upload a reference sheet for even better results (used as image-reference)." }),
    h("div", { id: "char-list" }, (b.characters || []).map(ch => charRow(ch))),
    characterForm(),
  ]);

  /* refs */
  const refCard = h("section", { class: "card" }, [
    h("h2", { text: "Style reference images" }),
    h("p", { class: "small muted", text: "Screenshots of the look you're going for. Sent alongside image prompts as visual guidance." }),
    h("div", { class: "ref-grid", id: "ref-grid" }, (b.reference_images || []).map(name => refTile(name))),
    h("div", { class: "row", style: "margin-top:.8rem" }, [
      h("label", { class: "btn", text: "⬆ Add reference images" }, [
        h("input", { type: "file", accept: "image/*", multiple: true, class: "hidden",
          onchange: async (ev) => {
            for (const f of ev.target.files) {
              const fd = new FormData(); fd.append("files", f);
              await fetch("/api/bible/refs", { method: "POST", body: fd });
            }
            state.bible = await api("/api/bible"); viewBible(state.bible);
            toast("References added", "good");
          } })]),
    ]),
  ]);

  view.append(styleCard, charCard, refCard);
}

function charRow(ch) {
  const img = ch.sheet ? h("img", { src: `/api/bible/media/${ch.sheet}` }) :
    h("div", { style: "width:56px;height:56px;border-radius:8px;border:1px dashed var(--line);display:flex;align-items:center;justify-content:center", text: "🙂" });
  return h("div", { class: "char-chip" }, [
    img,
    h("div", { style: "flex:1" }, [
      h("strong", { text: ch.name }),
      h("div", { class: "small muted", text: ch.description || "—" }),
    ]),
    h("div", { class: "row" }, [
      h("label", { class: "btn btn-sm", text: ch.sheet ? "Replace sheet" : "Upload sheet" }, [
        h("input", { type: "file", accept: "image/*", class: "hidden",
          onchange: async (ev) => {
            if (!ev.target.files.length) return;
            await apiUpload(`/api/bible/characters/${ch.id}/sheet`, ev.target.files[0]);
            state.bible = await api("/api/bible"); viewBible(state.bible);
          } })]),
      h("button", { class: "btn btn-sm", text: "Edit",
        onclick: async () => {
          const name = prompt("Character name:", ch.name); if (name === null) return;
          const description = prompt("Visual description (age, outfit, hair, props…):", ch.description);
          await api(`/api/bible/characters/${ch.id}`, { method: "PUT", body: { name: name || ch.name, description: description ?? ch.description } });
          state.bible = await api("/api/bible"); viewBible(state.bible);
        } }),
      h("button", { class: "btn btn-sm btn-bad", text: "✕",
        onclick: async () => {
          if (!confirm(`Remove ${ch.name}?`)) return;
          await api(`/api/bible/characters/${ch.id}`, { method: "DELETE" });
          state.bible = await api("/api/bible"); viewBible(state.bible);
        } }),
    ]),
  ]);
}

function characterForm() {
  const name = h("input", { type: "text", placeholder: "Name (e.g. The Explorer)" });
  const desc = h("input", { type: "text", placeholder: "Description (e.g. young explorer, beige safari shirt, brown hat, wide curious eyes)" });
  return h("div", { class: "input-row", style: "margin-top:.6rem" }, [
    name, desc,
    h("button", { class: "btn", text: "＋ Add character", style: "flex:0 0 auto",
      onclick: async () => {
        if (!name.value.trim()) return toast("Give the character a name", "warn");
        await api("/api/bible/characters", { body: { name: name.value, description: desc.value } });
        state.bible = await api("/api/bible"); viewBible(state.bible);
        toast("Character added", "good");
      } }),
  ]);
}

function refTile(name) {
  return h("div", { class: "ref" }, [
    h("img", { src: `/api/bible/media/${name}` }),
    h("button", { class: "del", text: "✕",
      onclick: async () => {
        await api(`/api/bible/refs/${name}`, { method: "DELETE" });
        state.bible = await api("/api/bible"); viewBible(state.bible);
      } }),
  ]);
}

/* =============================================================== SETTINGS */
async function viewSettings() {
  const view = $("#view");
  view.replaceChildren();
  const s = state.settings;
  const voices = state.voices || (state.voices = await api("/api/voices"));

  view.append(h("h1", { text: "Settings" }),
    h("p", { class: "muted", text: "Providers are fully model-configurable — when Google changes model availability, switch names here without any code changes. Keys are stored locally in ./data (never committed to git)." }));

  /* gemini */
  const keyInput = h("input", { type: "password", value: s.gemini_api_key || "", placeholder: "Google AI Studio API key" });
  const textModel = modelInput("model-text", s.text_model, ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash", "gemini-2.0-flash-lite"]);
  const imgModel = modelInput("model-img", s.image_model, ["gemini-2.5-flash-image", "gemini-2.0-flash-preview-image-generation"]);
  const imagenModel = modelInput("model-imagen", s.imagen_model, ["imagen-3.0-generate-002", "imagen-4.0-generate-001"]);
  const backendSel = h("select", { id: "img-backend" },
    [["gemini", "Gemini native image (conversational, keeps character refs)"],
     ["imagen", "Imagen (dedicated image model)"]].map(([v, l]) =>
      h("option", { value: v, text: l, ...(v === s.image_backend ? { selected: true } : {}) })));

  const geminiCard = h("section", { class: "card" }, [
    h("h2", { text: "Google AI Studio (Gemini)" }),
    h("label", { class: "field" }, [h("span", { text: "API key" }), keyInput]),
    h("div", { class: "grid2" }, [
      h("label", { class: "field" }, [h("span", { text: "Script / text model" }), textModel.input, textModel.list]),
      h("label", { class: "field" }, [h("span", { text: "Image backend" }), backendSel]),
      h("label", { class: "field" }, [h("span", { text: "Gemini image model" }), imgModel.input, imgModel.list]),
      h("label", { class: "field" }, [h("span", { text: "Imagen model" }), imagenModel.input, imagenModel.list]),
    ]),
  ]);

  /* voice */
  const providerSel = h("select", { id: "tts-provider" },
    [["edge-tts", "Edge TTS — free, natural neural voices (recommended)"],
     ["gtts", "Google TTS (gTTS) — free, simpler voice"]].map(([v, l]) =>
      h("option", { value: v, text: l, ...(v === s.tts_provider ? { selected: true } : {}) })));
  const edgeVoice = h("select", { id: "edge-voice" }, voices.map(v =>
    h("option", { value: v.id, text: v.label, ...(v.id === s.edge_voice ? { selected: true } : {}) })));
  const edgeRate = h("input", { type: "text", value: s.edge_rate || "+0%", placeholder: "+0% (e.g. +10%, -5%)" });
  const edgePitch = h("input", { type: "text", value: s.edge_pitch || "+0Hz", placeholder: "+0Hz (e.g. +5Hz, -10Hz)" });
  const gttsLang = h("select", { id: "gtts-lang" }, [
    ["en", "English"], ["en-us", "English (US)"], ["en-gb", "English (UK)"], ["es", "Spanish"],
    ["fr", "French"], ["de", "German"], ["it", "Italian"], ["pt", "Portuguese"], ["hi", "Hindi"],
    ["ar", "Arabic"], ["ru", "Russian"], ["ja", "Japanese"], ["ko", "Korean"], ["zh-CN", "Chinese (Simplified)"],
  ].map(([v, l]) => h("option", { value: v, text: l, ...(v === s.gtts_lang ? { selected: true } : {}) })));

  const voiceCard = h("section", { class: "card" }, [
    h("h2", { text: "Voice (text-to-speech)" }),
    h("div", { class: "grid2" }, [
      h("label", { class: "field" }, [h("span", { text: "Provider" }), providerSel]),
      h("label", { class: "field" }, [h("span", { text: "Edge TTS voice" }), edgeVoice]),
      h("label", { class: "field" }, [h("span", { text: "Edge rate" }), edgeRate]),
      h("label", { class: "field" }, [h("span", { text: "Edge pitch" }), edgePitch]),
      h("label", { class: "field" }, [h("span", { text: "gTTS language" }), gttsLang]),
    ]),
    h("div", { class: "row" }, [
      h("button", { class: "btn btn-sm", text: "↻ Refresh live voice list",
        onclick: async () => { state.voices = null; toast("Refreshing voice list…", "info", 1500); await viewSettings(); } }),
      h("button", { class: "btn btn-sm", text: "▶ Preview selected voice",
        onclick: async () => {
          await saveSettings();
          toast("Synthesizing preview…", "info", 1800);
          try {
            const r = await fetch("/api/settings/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ service: "tts" })});
            const d = await r.json();
            if (!r.ok) throw new Error(d.error || "failed");
            toast(d.message, "good");
          } catch (e) { toast(e.message, "bad"); }
        } }),
    ]),
  ]);

  /* mode */
  const mockChk = h("input", { type: "checkbox", id: "mock-mode", checked: s.mock_mode });
  const modeCard = h("section", { class: "card" }, [
    h("h2", { text: "Run mode" }),
    h("label", { class: "row", style: "cursor:pointer" }, [
      mockChk,
      h("span", { html: "<b>Mock mode (offline demo)</b> — generates placeholder scripts, painted preview cards and robot voice. The entire pipeline, timeline and CapCut export work end-to-end without any API key. Turn OFF for real production." }),
    ]),
  ]);

  /* tests + save */
  const testOut = h("div", { class: "small muted", id: "test-out" });
  async function runTest(service, label) {
    await saveSettings();
    testOut.textContent = `Testing ${label}…`;
    try {
      const r = await fetch("/api/settings/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ service })});
      const d = await r.json();
      if (!r.ok || d.ok === false) throw new Error(d.error || d.detail || "failed");
      testOut.textContent = `✓ ${d.message}`;
      toast(`${label}: OK`, "good");
    } catch (e) {
      testOut.textContent = `✗ ${label}: ${e.message}`;
      toast(`${label}: ${e.message}`, "bad", 7000);
    }
  }
  const testCard = h("section", { class: "card" }, [
    h("h2", { text: "Test connections" }),
    h("div", { class: "row" }, [
      h("button", { class: "btn", text: "Test script model", onclick: () => runTest("gemini_text", "Script model") }),
      h("button", { class: "btn", text: "Test image model", onclick: () => runTest("gemini_image", "Image model") }),
      h("button", { class: "btn", text: "Test voice (TTS)", onclick: () => runTest("tts", "TTS") }),
    ]),
    h("div", { style: "margin-top:.6rem" }, testOut),
  ]);

  const saveBtn = h("button", { class: "btn btn-primary btn-big", text: "💾 Save settings",
    onclick: async () => { await saveSettings(); toast("Settings saved", "good"); } });

  async function saveSettings() {
    state.settings = await api("/api/settings", { method: "PUT", body: {
      gemini_api_key: keyInput.value.trim(),
      text_model: textModel.input.value.trim() || "gemini-2.5-flash",
      image_backend: backendSel.value,
      image_model: imgModel.input.value.trim() || "gemini-2.5-flash-image",
      imagen_model: imagenModel.input.value.trim() || "imagen-3.0-generate-002",
      tts_provider: providerSel.value,
      edge_voice: edgeVoice.value,
      edge_rate: edgeRate.value.trim() || "+0%",
      edge_pitch: edgePitch.value.trim() || "+0Hz",
      gtts_lang: gttsLang.value,
      mock_mode: mockChk.checked,
    }});
    $("#mock-badge").classList.toggle("hidden", !state.settings.mock_mode);
  }

  view.append(geminiCard, voiceCard, modeCard, testCard, saveBtn);
}

function modelInput(id, value, suggestions) {
  const list = h("datalist", { id }, suggestions.map(sname => h("option", { value: sname })));
  const input = h("input", { type: "text", value, list: id, placeholder: "model name" });
  return { input, list };
}
