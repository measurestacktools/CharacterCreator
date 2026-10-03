"""Portrait tests — prompt/seed design, caching, failure fallback, hard-block skip."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch

import app as appmod
from fastapi.testclient import TestClient

client = TestClient(appmod.app)

SHEET = {
    "name": "Kaela Voss",
    "appearance": "Tall, silver-streaked hair, storm-grey coat.",
    "personality": "Sarcastic but loyal.",
    "backstory": "Ex star-pirate turned noodle vendor.",
    "abilities": ["Storm-calling"],
    "weaknesses": [],
    "goals": [],
    "catchphrases": [],
    "relationships": [],
    "stats": {},
    "scenario": "A floating market at dusk.",
}


def setup_function(_):
    appmod._current_character = None
    appmod.clear_portrait_cache()


def test_prompt_builder_uses_appearance_fields():
    sheet = appmod.normalize_sheet(dict(SHEET))
    prompt = appmod.build_portrait_prompt(sheet)
    assert "Kaela Voss" in prompt
    assert "silver-streaked hair" in prompt
    assert "floating market" in prompt
    assert len(prompt) <= 400


def test_seed_deterministic():
    assert appmod.portrait_seed("Kaela Voss") == appmod.portrait_seed("Kaela Voss")
    assert appmod.portrait_seed("Kaela Voss") == appmod.portrait_seed("KAELA VOSS")
    assert appmod.portrait_seed("Kaela Voss") != appmod.portrait_seed("Rook Kane")


class _FakeHeaders:
    def get_content_type(self):
        return "image/jpeg"


class _FakeResp:
    status = 200
    headers = _FakeHeaders()

    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_endpoint_caches_fetch_called_once_for_two_hits():
    appmod._current_character = appmod.normalize_sheet(dict(SHEET))
    calls = {"n": 0}

    def fake_urlopen(req, timeout=45):
        calls["n"] += 1
        return _FakeResp(b"\xff\xd8fakejpegbytes")

    with patch.object(appmod.urllib.request, "urlopen", side_effect=fake_urlopen):
        r1 = client.get("/api/portrait")
        r2 = client.get("/api/portrait")
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200
    assert r1.content == b"\xff\xd8fakejpegbytes"
    assert calls["n"] == 1


def test_failure_returns_404_json_not_traceback():
    appmod._current_character = appmod.normalize_sheet(dict(SHEET))

    def boom(req, timeout=45):
        raise TimeoutError("slow upstream")

    with patch.object(appmod.urllib.request, "urlopen", side_effect=boom):
        r = client.get("/api/portrait")
    assert r.status_code == 404
    assert "error" in r.json()
    assert "traceback" not in r.text.lower()


def test_hard_blocked_sheet_no_fetch_attempted():
    bad = dict(SHEET)
    bad["appearance"] = "explicit sexual detail step-by-step sexual intercourse scene"
    appmod._current_character = appmod.normalize_sheet(bad)

    def boom(req, timeout=45):  # pragma: no cover
        raise AssertionError("fetch must not be attempted for hard-blocked sheets")

    with patch.object(appmod.urllib.request, "urlopen", side_effect=boom):
        r = client.get("/api/portrait")
    assert r.status_code == 404
    assert "error" in r.json()


def test_no_character_404():
    appmod._current_character = None
    r = client.get("/api/portrait")
    assert r.status_code == 404
