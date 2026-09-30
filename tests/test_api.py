"""API tests — no live Groq calls (mocked OpenAI client)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
from unittest.mock import MagicMock, patch

import app as appmod
from fastapi.testclient import TestClient

client = TestClient(appmod.app)

SHEET = {
    "name": "Kaela Voss",
    "appearance": "Tall, silver-streaked hair.",
    "personality": "Sarcastic but loyal.",
    "backstory": "Ex star-pirate turned noodle vendor.",
    "abilities": ["Storm-calling"],
    "weaknesses": ["Afraid of silence"],
    "goals": ["Find her crew"],
    "catchphrases": ["Storms favor the bold."],
    "relationships": ["Brother Joren, rival"],
    "stats": {"strength": 70, "intelligence": 80, "charisma": 90, "agility": 60, "luck": 55, "magic": 75},
}


def _mock_client(json_text=None, chat_text="Ahoy, traveler."):
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


def test_validation_concept_required():
    appmod._runtime_key = "dummy"
    r = client.post("/api/create", json={"concept": ""})
    assert r.status_code == 422
    r = client.post("/api/create", json={"concept": "x" * 501})
    assert r.status_code == 422


def test_validation_chat_empty():
    r = client.post("/api/chat", json={"message": ""})
    assert r.status_code == 422


def test_json_fallback_defensive_parse():
    wrapped = "Here you go:\n```json\n" + json.dumps(SHEET) + "\n```\nEnjoy!"
    parsed = appmod.parse_json_defensive(wrapped)
    assert parsed["name"] == "Kaela Voss"
    norm = appmod.normalize_sheet(parsed)
    assert norm["stats"]["strength"] == 70


def test_json_fallback_malformed_raises():
    try:
        appmod.parse_json_defensive("not json at all ((((")
        assert False, "should raise"
    except ValueError:
        pass


def test_chat_no_character_guard():
    appmod._current_character = None
    appmod._runtime_key = "dummy"
    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 400
    assert "Create" in r.json()["error"] or "character" in r.json()["error"].lower()


def test_modify_no_character_guard():
    appmod._current_character = None
    r = client.post("/api/modify", json={"instruction": "make older"})
    assert r.status_code == 400


def test_export_no_character_404():
    appmod._current_character = None
    r = client.get("/api/export")
    assert r.status_code == 404


def test_create_modify_export_chat_flow_mocked():
    mock = _mock_client(json_text=json.dumps(SHEET))
    with patch.object(appmod, "make_client", return_value=mock):
        appmod._runtime_key = "dummy"
        r = client.post("/api/create", json={"concept": "star pirate noodle vendor"})
        assert r.status_code == 200, r.text
        assert r.json()["character"]["name"] == "Kaela Voss"

        r = client.post("/api/modify", json={"instruction": "give her a scar"})
        assert r.status_code == 200

        r = client.get("/api/export")
        assert r.status_code == 200
        assert "Kaela Voss" in r.json()["markdown"]

    chat_mock = _mock_client(chat_text="Storms favor the bold, friend.")
    with patch.object(appmod, "make_client", return_value=chat_mock):
        r = client.post("/api/chat", json={"message": "hello"})
        assert r.status_code == 200
        assert "Storms" in r.json()["reply"]

        r = client.post("/api/reset")
        assert r.status_code == 200


def test_status_presence_only():
    appmod._runtime_key = None
    os.environ.pop("GROQ_API_KEY", None)
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["has_key"] is False
    assert "gsk" not in r.text.lower()
