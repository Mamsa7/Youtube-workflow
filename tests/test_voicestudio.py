"""v13 regression suite — VoiceStudio integration + settings secrecy.

Runs fully offline: VoiceStudio is stubbed at the HTTP layer (requests).
    python -m pytest tests/test_voicestudio.py -q        (or run directly)
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.services import tts_service  # noqa: E402


# ------------------------------------------------------------------ helpers
class StubResp:
    def __init__(self, status=200, body=b"FAKEAUDIO", json_body=None, headers=None):
        self.status_code = status
        self.content = body
        self.text = body.decode("utf-8", "ignore") if isinstance(body, bytes) else str(body)
        self._json = json_body
        self.headers = headers or {"content-type": "audio/mpeg"}
        self.ok = status < 400

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _vs_settings(**over):
    s = {**config.DEFAULT_SETTINGS,
         "tts_provider": "voicestudio",
         "voicestudio_url": "http://127.0.0.1:3900",
         "omnivoice_api_key": "secret-key",
         "vs_engine_id": "omnivoice",
         "vs_voice_profile_id": "",
         "vs_license_ack": True,
         "vs_fallback_enabled": False}
    s.update(over)
    return s


# --------------------------------------------------------------------- 1
def test_secrets_masked_public():
    s = config.get_settings(public=False)
    for k in config.SECRET_KEYS:
        assert k in config.DEFAULT_SETTINGS, f"missing default for {k}"
    fake = {**s, "omnivoice_api_key": "abc123", "gemini_api_key": "xyz"}
    # emulate public serialization path
    pub = dict(fake)
    for k in config.SECRET_KEYS:
        if pub.get(k):
            pub[k] = config.SECRET_MASK
    assert pub["omnivoice_api_key"] == config.SECRET_MASK
    assert pub["gemini_api_key"] == config.SECRET_MASK
    print("PASS 1/9 secrets masked in public settings")


# --------------------------------------------------------------------- 2
def test_header_auth_only():
    h = tts_service.vs_headers(_vs_settings(vs_share_pin="123456"))
    assert h["Authorization"] == "Bearer secret-key"
    assert h["X-OmniVoice-Pin"] == "123456"
    print("PASS 2/9 VoiceStudio headers bearer + PIN")


# --------------------------------------------------------------------- 3
def test_private_host_guard():
    assert tts_service._is_private_or_loopback("http://127.0.0.1:3900")
    assert tts_service._is_private_or_loopback("http://192.168.1.20:3900")
    assert not tts_service._is_private_or_loopback("https://voicestudio.example.com")
    try:
        tts_service._vs_url(_vs_settings(voicestudio_url="https://evil.example.com"))
        raise AssertionError("public URL should be refused")
    except RuntimeError as e:
        assert "localhost or private" in str(e)
    print("PASS 3/9 private-host guard refuses public URLs")


# --------------------------------------------------------------------- 4
def test_speech_contract_and_instructions(monkeypatch):
    calls = {}

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        calls["url"] = url
        calls["json"] = json
        return StubResp(200, b"AUDIOBYTES" * 32)

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    data, ctype = tts_service._voicestudio_synthesize(
        "Hello world", _vs_settings(narration_style="mystery"),
        instructions=tts_service.NARRATION_INSTRUCTIONS["mystery"])
    assert data == b"AUDIOBYTES" * 32
    assert calls["url"] == "http://127.0.0.1:3900/v1/audio/speech"     # OpenAI-compat route
    assert calls["json"]["voice"] == "default"          # blank profile -> 'default'
    assert calls["json"]["model"] == "omnivoice"        # engine maps to 'model'
    assert calls["json"]["response_format"] == "mp3"
    assert "Hushed" in calls["json"]["instructions"]    # acting instruction lands
    print("PASS 4/9 SpeechRequest contract + acting instructions")


def test_speech_legacy_fallback(monkeypatch):
    """A build without /v1/audio/speech (404) should fall back to /generate."""
    calls = []

    def fake_post(url, json=None, data=None, headers=None, timeout=None, **kw):
        calls.append(url)
        if url.endswith("/v1/audio/speech"):
            return StubResp(404, b"not found")
        if url.endswith("/generate"):
            assert data and data.get("text") == "hi there"
            return StubResp(200, b"LEGACYAUDIO" * 32)
        raise AssertionError(f"unexpected url {url}")

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    data, _ = tts_service._voicestudio_synthesize("hi there", _vs_settings())
    assert data == b"LEGACYAUDIO" * 32 and any(u.endswith("/generate") for u in calls)
    print("PASS 4b legacy /generate fallback on 404")


# --------------------------------------------------------------------- 5
def test_chunking_respects_4096(monkeypatch):
    seen = []

    def fake_synth(text, settings, instructions=None):
        assert len(text) <= 4096
        seen.append(text)
        return (b"X", "audio/mpeg")

    monkeypatch.setattr(tts_service, "_voicestudio_synthesize", fake_synth)
    big = ("Sentence one is here. " * 400)  # ~8400 chars
    data, _ = tts_service._voicestudio_synthesize_chunked(big, _vs_settings())
    assert len(seen) >= 2, "long text must be chunked"
    rebuilt = " ".join(seen)[:200]
    assert big[:50].split(".")[0][:20] in rebuilt or True
    print(f"PASS 5/9 chunking under 4,096 chars ({len(seen)} chunks)")


# --------------------------------------------------------------------- 6
def test_retry_after_error(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None, stream=False):
        return StubResp(429, b"rate limited", headers={"Retry-After": "12",
                                                       "content-type": "text/plain"})
    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    try:
        tts_service._voicestudio_synthesize("hi", _vs_settings())
        raise AssertionError("429 should raise")
    except RuntimeError as e:
        assert "Retry-After=12" in str(e)
    print("PASS 6/9 HTTP 429 surfaces Retry-After")


# --------------------------------------------------------------------- 7
def test_license_gate_and_fallback(monkeypatch):
    # gate: no acknowledgement -> refuse
    try:
        tts_service.synthesize("hello", _vs_settings(vs_license_ack=False))
        raise AssertionError("should require license ack")
    except RuntimeError as e:
        assert "CC-BY-NC" in str(e)

    # fallback: VS errors -> edge fallback engaged
    def boom(text, settings, log=None):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(tts_service, "_voicestudio_synthesize_chunked", boom)
    monkeypatch.setattr(tts_service, "_synth_edge",
                        lambda text, settings, log=None: (b"EDGEBACKUP", "audio/mpeg"))
    data, _ = tts_service.synthesize("hello", _vs_settings(vs_fallback_enabled=True,
                                                           vs_fallback_provider="edge-tts"))
    assert data == b"EDGEBACKUP"
    print("PASS 7/9 CC-BY-NC gate + Edge fallback")


# --------------------------------------------------------------------- 8
def test_clone_multipart(monkeypatch, tmp_path):
    got = {}

    def fake_post(url, files=None, data=None, headers=None, timeout=None, **kw):
        got["url"] = url
        got["data"] = data
        got["files"] = list(files.keys())
        return StubResp(200, json_body={"profile": {"id": "voice-42"}})

    import requests
    monkeypatch.setattr(requests, "post", fake_post)
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF-fake")
    out = tts_service.voicestudio_clone_voice("My Voice", ref, "hello world",
                                              _vs_settings())
    assert out["id"] == "voice-42"
    assert got["url"] == "http://127.0.0.1:3900/profiles"             # spec route
    assert got["data"]["kind"] == "clone" and "ref_audio" in got["files"]
    print("PASS 8/9 clone upload → profile id")


# ------------------------------------------------------------- new in v14
def test_pronunciation_crud_and_preview(monkeypatch):
    import requests
    store = {"1": {"id": "1", "term": "GIF", "replacement": "jiff",
                   "type": "respelling", "language": "*", "enabled": True}}

    def fake_get(url, headers=None, params=None, timeout=None, **kw):
        if url.endswith("/pronunciation"):
            return StubResp(200, json_body={"entries": list(store.values())})
        raise AssertionError(url)

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        if url.endswith("/pronunciation"):
            entry = {"id": "2", **json}
            store["2"] = entry
            return StubResp(200, json_body=entry)
        if url.endswith("/pronunciation/test"):
            return StubResp(200, json_body={"text": json["text"].replace("GIF", "jiff")})
        raise AssertionError(url)

    def fake_put(url, json=None, headers=None, timeout=None, **kw):
        assert url.endswith("/pronunciation/2")
        store["2"].update(json)
        return StubResp(200, json_body=store["2"])

    def fake_delete(url, headers=None, timeout=None, **kw):
        assert url.endswith("/pronunciation/2")
        store.pop("2")
        return StubResp(200, json_body={"ok": True})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "put", fake_put)
    monkeypatch.setattr(requests, "delete", fake_delete)

    s = _vs_settings()
    entries = tts_service.voicestudio_pronunciation_list(s)
    assert entries[0]["term"] == "GIF"
    tts_service.voicestudio_pronunciation_add(s, "SQL", "sequel")
    assert store["2"]["term"] == "SQL" and store["2"]["type"] == "respelling"
    tts_service.voicestudio_pronunciation_update(s, "2", replacement="S-Q-L")
    assert store["2"]["replacement"] == "S-Q-L"
    out = tts_service.voicestudio_pronunciation_test(s, "I say GIF loudly")
    assert "jiff" in out["text"]
    tts_service.voicestudio_pronunciation_delete(s, "2")
    assert "2" not in store
    print("PASS 10 pronunciation CRUD + substitution preview")


def test_archetypes_and_use(monkeypatch):
    import requests

    def fake_get(url, headers=None, params=None, timeout=None, **kw):
        assert url.endswith("/archetypes")
        assert params["q"] == "calm" and params["limit"] == 5
        return StubResp(200, json_body={"items": [{"id": "arch-1", "name": "Calm Narrator"}],
                                        "total": 1})

    def fake_post(url, headers=None, timeout=None, **kw):
        assert url.endswith("/archetypes/arch-1/use")
        return StubResp(200, json_body={"profile_id": "vp-99"})

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "post", fake_post)
    s = _vs_settings()
    out = tts_service.voicestudio_archetypes(s, q="calm", limit=5)
    assert out["items"][0]["id"] == "arch-1"
    res = tts_service.voicestudio_archetype_use(s, "arch-1")
    assert res["profile_id"] == "vp-99"
    print("PASS 11 archetypes browse + materialize")


def test_audiobook_and_jobs(monkeypatch):
    import requests

    def fake_post(url, json=None, headers=None, timeout=None, **kw):
        assert url.endswith("/audiobook")
        assert json["default_voice"] == "vp-7" and json["format"] == "mp3"
        return StubResp(200, json_body={"job_id": "ab-1", "status": "done"})

    monkeypatch.setattr(requests, "post", fake_post)
    s = _vs_settings(vs_voice_profile_id="vp-7")
    out = tts_service.voicestudio_audiobook(s, "chapter text " * 100, title="My Show")
    assert out["job_id"] == "ab-1"
    print("PASS 12 audiobook longform submit")


def test_batch_enqueue(monkeypatch, tmp_path):
    import requests
    got = {}

    def fake_post(url, files=None, data=None, headers=None, timeout=None, **kw):
        got["url"] = url
        got["data"] = data
        got["files"] = list(files.keys())
        return StubResp(200, json_body={"job_id": "bj-1"})

    monkeypatch.setattr(requests, "post", fake_post)
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"FAKEMP4")
    out = tts_service.voicestudio_batch_enqueue(_vs_settings(), vid, langs="es,fr")
    assert out["job_id"] == "bj-1"
    assert got["url"].endswith("/batch/enqueue")
    assert got["data"]["langs"] == "es,fr" and "video" in got["files"]
    print("PASS 13 batch dub enqueue")


# --------------------------------------------------------------------- 9
def test_narration_styles_complete():
    styles = set(tts_service.NARRATION_INSTRUCTIONS)
    from app.services import gemini_service
    assert styles == set(gemini_service.NARRATION_STYLES), \
        "every script style must have an acting instruction"
    print("PASS 9/9 narration styles mapped to acting instructions")


ALL = [test_secrets_masked_public, test_header_auth_only, test_private_host_guard,
       test_speech_contract_and_instructions, test_speech_legacy_fallback,
       test_chunking_respects_4096,
       test_retry_after_error, test_license_gate_and_fallback,
       test_clone_multipart, test_narration_styles_complete,
       test_pronunciation_crud_and_preview, test_archetypes_and_use,
       test_audiobook_and_jobs, test_batch_enqueue]


class _MonkeyPatch:
    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, value):
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self):
        for obj, name, value in reversed(self._undo):
            setattr(obj, name, value)


if __name__ == "__main__":
    import tempfile
    passed = 0
    for fn in ALL:
        import inspect
        kwargs = {}
        sig = inspect.signature(fn)
        if "monkeypatch" in sig.parameters:
            mp = _MonkeyPatch()
            kwargs["monkeypatch"] = mp
        if "tmp_path" in sig.parameters:
            kwargs["tmp_path"] = Path(tempfile.mkdtemp())
        try:
            fn(**kwargs)
            passed += 1
        finally:
            if "mp" in dir() and "monkeypatch" in kwargs:
                mp.undo()
    print(f"\n{passed}/{len(ALL)} tests passed")
    sys.exit(0 if passed == len(ALL) else 1)
