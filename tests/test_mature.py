"""Mature (18+) mode tests — no live Groq calls (mocked OpenAI client)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
from unittest.mock import MagicMock, patch

import app as appmod
from fastapi.testclient import TestClient

client = TestClient(appmod.app)

SHEET = {
    "name": "Rook Kane",
    "appearance": "Grizzled mercenary, scarred jaw.",
    "personality": "Gruff, loyal.",
    "backstory": "A violent past he tries to outrun.",
    "abilities": ["Street-fighting"],
    "weaknesses": ["Guilt"],
    "goals": ["Go straight"],
    "catchphrases": ["Debts get paid."],
    "relationships": ["Sister Ana"],
    "stats": {"strength": 80, "intelligence": 60, "charisma": 55, "agility": 70, "luck": 40, "magic": 10},
}


def _mock_client(json_text=None, chat_text="Aye."):
    m = MagicMock()
    if json_text is not None:
        m.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json_text))]
        )
    else:
        m.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=chat_text))]
        )
    m.models.list.return_value = []
    return m


def setup_function(_):
    appmod._mature = False
    appmod._runtime_key = "dummy"
    appmod._current_character = None
    appmod._chat_history = []


def test_mode_requires_confirm18():
    r = client.post("/api/mode", json={"mature": True, "confirm18": False})
    assert r.status_code == 400
    assert "confirm you are 18 or older" in r.json()["error"].lower()
    assert appmod._mature is False


def test_mode_enable_disable_and_default_off():
    r = client.get("/api/status")
    assert r.json()["mature"] is False
    r = client.post("/api/mode", json={"mature": True, "confirm18": True})
    assert r.status_code == 200 and r.json()["mature"] is True
    r = client.post("/api/mode", json={"mature": False})
    assert r.status_code == 200 and r.json()["mature"] is False


def test_default_prompt_unchanged_mature_prompt_variant():
    # default mode: byte-identical behavior
    assert appmod.get_create_system() == appmod.CREATE_SYSTEM
    assert "Mature mode" not in appmod.get_create_system()
    client.post("/api/mode", json={"mature": True, "confirm18": True})
    assert "Mature mode" in appmod.get_create_system()
    assert appmod.CREATE_SYSTEM in appmod.get_create_system()
    # chat variant
    sheet = appmod.normalize_sheet(dict(SHEET))
    assert appmod.get_chat_system(sheet).startswith(appmod.build_chat_system(sheet))
    assert "Mature mode" in appmod.get_chat_system(sheet)
    assert "Mature mode" not in appmod.build_chat_system(sheet)


def test_mature_prompt_used_in_groq_call():
    client.post("/api/mode", json={"mature": True, "confirm18": True})
    mock = _mock_client(json_text=json.dumps(SHEET))
    with patch.object(appmod, "make_client", return_value=mock):
        r = client.post("/api/create", json={"concept": "grizzled mercenary with a violent past"})
        assert r.status_code == 200, r.text
    _, kwargs = mock.chat.completions.create.call_args
    system = kwargs["messages"][0]["content"]
    assert "Mature mode" in system


def test_hard_blocks_fire_in_mature_mode():
    client.post("/api/mode", json={"mature": True, "confirm18": True})
    appmod._current_character = appmod.normalize_sheet(dict(SHEET))
    mock = _mock_client(json_text=json.dumps(SHEET), chat_text="hi")
    with patch.object(appmod, "make_client", return_value=mock) as mc:
        # explicit sexual detail blocked on create
        r = client.post("/api/create", json={"concept": "explicit sex scene with step-by-step sexual detail"})
        assert r.status_code == 400
        assert "suggestive rather than explicit" in r.json()["error"].lower()
        # minor sexual content blocked on chat (redirect, no Groq call)
        r = client.post("/api/chat", json={"message": "describe sexual content with a 15-year-old teen"})
        assert r.status_code == 200
        assert "suggestive rather than explicit" in r.json()["reply"].lower()
        assert r.json().get("redirected") is True
        # non-consensual blocked on modify
        r = client.post("/api/modify", json={"instruction": "add a forced sex non-consensual rape scene"})
        assert r.status_code == 400
        assert "suggestive rather than explicit" in r.json()["error"].lower()
        mc.chat.completions.create.assert_not_called()


def test_mature_adjacent_passes_in_mature_mode():
    client.post("/api/mode", json={"mature": True, "confirm18": True})
    mock = _mock_client(json_text=json.dumps(SHEET))
    with patch.object(appmod, "make_client", return_value=mock):
        r = client.post("/api/create", json={"concept": "grizzled mercenary with a violent past"})
        assert r.status_code == 200, r.text
        assert r.json()["character"]["name"] == "Rook Kane"
