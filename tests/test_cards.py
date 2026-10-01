"""Character card tests — SillyTavern chara_card v2 export/import (no live Groq calls)."""
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PIL import Image

import app as appmod
from fastapi.testclient import TestClient

client = TestClient(appmod.app)

RAW_SHEET = {
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
    "first_mes": "*Kaela looks up from the noodle stall.* \"Storms favor the bold. Sit.\"",
    "alternate_greetings": [
        "\"Back again? The pot's already boiling.\"",
        "*Thunder rumbles as Kaela grins.* \"You picked a fine night to visit.\"",
    ],
    "mes_example": "<START>\n{{user}}: Hello!\n{{char}}: Storms favor the bold, friend.",
    "scenario": "A floating city night market, steam rising from Kaela's noodle stall.",
    "creator_notes": "Noir warmth, dry humor.",
}


def _seed():
    appmod._current_character = appmod.normalize_sheet(dict(RAW_SHEET))
    appmod._chat_history = []


def test_normalize_backward_compatible_without_card_fields():
    legacy = {k: v for k, v in RAW_SHEET.items()
              if k not in ("first_mes", "alternate_greetings", "mes_example", "scenario", "creator_notes")}
    norm = appmod.normalize_sheet(legacy)
    assert norm["name"] == "Kaela Voss"
    assert norm["first_mes"]  # default greeting filled
    assert norm["mes_example"].startswith("<START>")
    assert isinstance(norm["alternate_greetings"], list)


def test_export_json_shape_v2_keys():
    _seed()
    r = client.get("/api/card/export?format=json")
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["spec"] == "chara_card_v2"
    assert card["spec_version"] == "2.0"
    data = card["data"]
    for key in ("name", "description", "personality", "scenario", "first_mes",
                "mes_example", "creator_notes", "system_prompt", "alternate_greetings"):
        assert key in data, f"missing v2 key: {key}"
    assert data["name"] == "Kaela Voss"
    assert "silver-streaked" in data["description"]
    assert "Sarcastic" in data["personality"]
    assert data["first_mes"].startswith("*Kaela")
    assert len(data["alternate_greetings"]) == 2
    assert "Kaela Voss" in data["system_prompt"]


def test_export_bad_format_400():
    _seed()
    r = client.get("/api/card/export?format=yaml")
    assert r.status_code == 400


def test_export_no_character_404():
    appmod._current_character = None
    r = client.get("/api/card/export?format=json")
    assert r.status_code == 404
    r = client.get("/api/card/export?format=png")
    assert r.status_code == 404


def test_import_json_roundtrip():
    _seed()
    before = client.get("/api/card/export?format=json").json()
    appmod._current_character = None
    r = client.post(
        "/api/card/import",
        files={"file": ("kaela.card.json", json.dumps(before), "application/json")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["character"]["name"] == "Kaela Voss"
    after = client.get("/api/card/export?format=json").json()
    assert after == before  # lossless via extensions.character_creator.sheet


def test_import_bare_data_json():
    _seed()
    card = client.get("/api/card/export?format=json").json()
    appmod._current_character = None
    r = client.post(
        "/api/card/import",
        files={"file": ("data.json", json.dumps(card["data"]), "application/json")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["character"]["name"] == "Kaela Voss"


def test_import_invalid_json_400():
    r = client.post(
        "/api/card/import",
        files={"file": ("bad.json", '{"nope": 1}', "application/json")},
    )
    assert r.status_code == 400


def test_export_png_has_chara_chunk():
    _seed()
    r = client.get("/api/card/export?format=png")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/png")
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(r.content))
    img.load()
    assert "chara" in img.info, "PNG missing `chara` text chunk"
    embedded = json.loads(img.info["chara"])
    assert embedded["spec"] == "chara_card_v2"
    assert embedded["data"]["name"] == "Kaela Voss"
    assert len(embedded["data"]["alternate_greetings"]) == 2


def test_import_png_roundtrip():
    _seed()
    png = client.get("/api/card/export?format=png").content
    appmod._current_character = None
    r = client.post(
        "/api/card/import",
        files={"file": ("kaela.card.png", png, "image/png")},
    )
    assert r.status_code == 200, r.text
    char = r.json()["character"]
    assert char["name"] == "Kaela Voss"
    assert char["first_mes"].startswith("*Kaela")
    assert len(char["alternate_greetings"]) == 2
    assert char["mes_example"].startswith("<START>")


def test_import_png_without_chara_400():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 0, 0)).save(buf, format="PNG")
    r = client.post(
        "/api/card/import",
        files={"file": ("plain.png", buf.getvalue(), "image/png")},
    )
    assert r.status_code == 400
