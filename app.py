"""AI Character Creator — FastAPI backend (port 8012).

Stack: Python + FastAPI + vanilla HTML/CSS/JS.
LLM: OpenAI SDK pointed at https://api.groq.com/openai/v1
Model: openai/gpt-oss-120b

Key policy:
- Settings modal POSTs key once to /api/key for live verification (models.list).
- Server keeps it in process-global memory only (never written to disk).
- DELETE /api/key clears memory. .env GROQ_API_KEY is fallback.
- /api/status reports presence only. Frontend never stores/sends keys.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from openai import OpenAI, APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError, RateLimitError
from pydantic import BaseModel, field_validator

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

MODEL = os.getenv("MODEL", "openai/gpt-oss-120b").strip() or "openai/gpt-oss-120b"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
MAX_HISTORY_MESSAGES = 20  # last 10 turns (user+assistant pairs)

app = FastAPI(title="AI Character Creator")

# ---- process-global server memory only ----
_runtime_key: Optional[str] = None
_current_character: Optional[Dict[str, Any]] = None
_chat_history: List[Dict[str, str]] = []


# ---------- key helpers ----------
def effective_key() -> str:
    if _runtime_key and _runtime_key.strip():
        return _runtime_key.strip()
    return (os.getenv("GROQ_API_KEY", "") or "").strip()


def make_client(api_key: str) -> OpenAI:
    return OpenAI(base_url=GROQ_BASE_URL, api_key=api_key)


def groq_error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, AuthenticationError):
        return JSONResponse(status_code=401, content={"error": "Invalid API key. Check your Groq key in Settings."})
    if isinstance(exc, RateLimitError):
        return JSONResponse(status_code=429, content={"error": "Groq rate limit hit. Wait a moment and try again."})
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return JSONResponse(status_code=503, content={"error": "Could not reach Groq. Check your connection and retry."})
    if isinstance(exc, APIStatusError):
        code = exc.status_code or 502
        if code == 401:
            return JSONResponse(status_code=401, content={"error": "Invalid API key. Check your Groq key in Settings."})
        if code == 429:
            return JSONResponse(status_code=429, content={"error": "Groq rate limit hit. Wait a moment and try again."})
        if code in (400, 404):
            return JSONResponse(status_code=502, content={"error": f"Groq request rejected ({code}). Try rephrasing."})
        return JSONResponse(status_code=502, content={"error": f"Groq error ({code}). Please retry."})
    msg = str(exc)[:300] or "Unknown error"
    low = msg.lower()
    if "401" in low or "invalid" in low or "unauthorized" in low:
        return JSONResponse(status_code=401, content={"error": "Invalid API key. Check your Groq key in Settings."})
    if "429" in low or "rate" in low:
        return JSONResponse(status_code=429, content={"error": "Groq rate limit hit. Wait a moment and try again."})
    return JSONResponse(status_code=502, content={"error": f"AI service error. Please retry. ({msg[:120]})"})


# ---------- sheet helpers ----------
STAT_KEYS = ["strength", "intelligence", "charisma", "agility", "luck", "magic"]
LIST_KEYS = ["abilities", "weaknesses", "goals", "catchphrases", "relationships"]


def clamp_stat(v: Any) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 50
    return max(1, min(100, n))


def as_str_list(v: Any, limit: int = 8) -> List[str]:
    if not isinstance(v, list):
        return []
    out: List[str] = []
    for item in v[:limit]:
        s = str(item).strip()
        if s:
            out.append(s[:200])
    return out


def normalize_sheet(raw: Dict[str, Any]) -> Dict[str, Any]:
    stats = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
    name = str(raw.get("name", "Unnamed")).strip()[:80] or "Unnamed"
    # Chat/card fields are optional (older sheets / third-party cards may lack
    # them) — fill deterministic defaults so parsing stays backward-compatible.
    first_mes = str(raw.get("first_mes", "")).strip()[:1500] or f"Hello. I'm {name}."
    mes_example = (
        str(raw.get("mes_example", "")).strip()[:3000]
        or f"<START>\n{{{{user}}}}: Hello!\n{{{{char}}}}: {first_mes}"
    )
    norm = {
        "name": name,
        "appearance": str(raw.get("appearance", "")).strip()[:1500] or "Mysterious figure.",
        "personality": str(raw.get("personality", "")).strip()[:1500] or "Complex and intriguing.",
        "backstory": str(raw.get("backstory", "")).strip()[:3000] or "Origins unknown.",
        "abilities": as_str_list(raw.get("abilities")),
        "weaknesses": as_str_list(raw.get("weaknesses")),
        "goals": as_str_list(raw.get("goals")),
        "catchphrases": as_str_list(raw.get("catchphrases")),
        "relationships": as_str_list(raw.get("relationships")),
        "stats": {k: clamp_stat((stats or {}).get(k, 50)) for k in STAT_KEYS},
        "first_mes": first_mes,
        "alternate_greetings": as_str_list(raw.get("alternate_greetings"), limit=4),
        "mes_example": mes_example,
        "scenario": str(raw.get("scenario", "")).strip()[:1500],
        "creator_notes": str(raw.get("creator_notes", "")).strip()[:1500],
    }
    return norm


def parse_json_defensive(text: str) -> Dict[str, Any]:
    """Defensive JSON parse: direct, then largest {...} substring, then quote repair."""
    if not text or not text.strip():
        raise ValueError("Empty response from AI.")
    t = text.strip()
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # largest {...} block
    matches = re.findall(r"\{.*\}", t, flags=re.DOTALL)
    for cand in sorted(matches, key=len, reverse=True)[:3]:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    # last resort: strip code fences then retry
    cleaned = re.sub(r"^```(?:json)?|```$", "", t.strip(), flags=re.MULTILINE).strip()
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    raise ValueError("AI returned malformed data. Please regenerate.")


CREATE_SYSTEM = (
    "You are a creative character designer for games, novels and roleplay. "
    "Return ONLY valid JSON, no markdown, no commentary. Exact schema:\n"
    '{"name": str, "appearance": str, "personality": str, "backstory": str, '
    '"abilities": [str], "weaknesses": [str], "goals": [str], '
    '"catchphrases": [str], "relationships": [str], '
    '"stats": {"strength": 1-100, "intelligence": 1-100, "charisma": 1-100, '
    '"agility": 1-100, "luck": 1-100, "magic": 1-100}, '
    '"first_mes": str (one in-character opening greeting), '
    '"alternate_greetings": [str, str] (exactly 2 more distinct in-character greetings), '
    '"mes_example": str (short example dialogue using {{user}} / {{char}} lines, starting with <START>), '
    '"scenario": str (1-2 sentence current situation or setting)}\n'
    "Make the character feel unique and personal, grounded in the user's concept. "
    "2-5 items per list. Stats must be integers 1-100. "
    "Always include first_mes, alternate_greetings (2 entries), mes_example and scenario; "
    "older readers ignore unknown keys, so extra keys are safe."
)


def build_create_prompt(d: Dict[str, str]) -> str:
    lines = [f"Concept: {d['concept']}"]
    if d.get("world"):
        lines.append(f"World/setting: {d['world']}")
    if d.get("personality"):
        lines.append(f"Personality hints: {d['personality']}")
    if d.get("powers"):
        lines.append(f"Powers/skills: {d['powers']}")
    if d.get("role"):
        lines.append(f"Role: {d['role']}")
    if d.get("genre"):
        lines.append(f"Genre: {d['genre']}")
    lines.append("Create a full memorable character sheet as JSON per schema.")
    return "\n".join(lines)


def build_chat_system(sheet: Dict[str, Any]) -> str:
    return (
        f"You are roleplaying as {sheet.get('name', 'the character')}. Stay fully in character at all times. "
        f"Never break character, never mention you are an AI.\n"
        f"Personality: {sheet.get('personality', '')}\n"
        f"Appearance: {sheet.get('appearance', '')}\n"
        f"Backstory: {sheet.get('backstory', '')}\n"
        f"Abilities: {', '.join(sheet.get('abilities', []))}\n"
        f"Weaknesses: {', '.join(sheet.get('weaknesses', []))}\n"
        f"Goals: {', '.join(sheet.get('goals', []))}\n"
        f"Catchphrases (weave in naturally): {', '.join(sheet.get('catchphrases', []))}\n"
        f"Relationships: {', '.join(sheet.get('relationships', []))}\n"
        f"Stats: {json.dumps(sheet.get('stats', {}))}\n"
        "Rules: answer as the character in first person voice, keep replies vivid but under 180 words, "
        "reflect your personality, goals and speech style."
    )


def build_markdown(sheet: Dict[str, Any]) -> str:
    s = sheet
    lines = [f"# {s.get('name', 'Unnamed')}", ""]
    lines += [f"**Appearance:** {s.get('appearance', '')}", "", f"**Personality:** {s.get('personality', '')}", ""]
    lines += [f"## Backstory", str(s.get("backstory", "")), ""]
    for key, title in [("abilities", "Abilities"), ("weaknesses", "Weaknesses"), ("goals", "Goals"),
                       ("catchphrases", "Catchphrases"), ("relationships", "Relationships")]:
        items = s.get(key, []) or []
        lines.append(f"## {title}")
        if items:
            lines += [f"- {x}" for x in items]
        else:
            lines.append("_None_")
        lines.append("")
    lines.append("## Stats")
    for k in STAT_KEYS:
        lines.append(f"- {k.capitalize()}: {(s.get('stats') or {}).get(k, 50)}/100")
    lines.append("")
    return "\n".join(lines)


# ---------- SillyTavern chara_card v2 helpers ----------
# Spec: PNG with the card JSON stored as a raw (unencoded) string in a tEXt
# chunk named `chara`, plus plain .json files of the same shape.
# NOTE: chara_card v3 base64-encodes the JSON before embedding; we implement
# the v2 string form on export, but accept v3-style base64 on import.
CARD_SPEC = "chara_card_v2"
CARD_SPEC_VERSION = "2.0"
CARD_REQUIRED_DATA_KEYS = [
    "name", "description", "personality", "scenario", "first_mes",
    "mes_example", "creator_notes", "system_prompt", "alternate_greetings",
]
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
CARD_UPLOAD_LIMIT = 10 * 1024 * 1024


def slugify(name: str, fallback: str = "character") -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or fallback).lower()).strip("-") or fallback


def sheet_to_card(sheet: Dict[str, Any]) -> Dict[str, Any]:
    """Build a chara_card v2 payload from our in-session character sheet."""
    name = str(sheet.get("name", "Unnamed")).strip() or "Unnamed"
    abilities = ", ".join(sheet.get("abilities", []) or [])
    weaknesses = ", ".join(sheet.get("weaknesses", []) or [])
    goals = ", ".join(sheet.get("goals", []) or [])
    catchphrases = ", ".join(sheet.get("catchphrases", []) or [])
    relationships = ", ".join(sheet.get("relationships", []) or [])
    description = (
        f"Appearance: {sheet.get('appearance', '')}\n"
        f"Backstory: {sheet.get('backstory', '')}\n"
        f"Abilities: {abilities}\n"
        f"Weaknesses: {weaknesses}"
    ).strip()
    personality = str(sheet.get("personality", "") or "").strip()
    if catchphrases:
        personality = f"{personality}\nCatchphrases: {catchphrases}".strip()
    scenario = str(sheet.get("scenario", "") or "").strip() or (
        f"Goals: {goals}\nRelationships: {relationships}".strip() or "An open roleplay scene."
    )
    data = {
        "name": name,
        "description": description,
        "personality": personality or "Complex and intriguing.",
        "scenario": scenario,
        "first_mes": str(sheet.get("first_mes", "") or f"Hello. I'm {name}."),
        "mes_example": str(sheet.get("mes_example", "") or ""),
        "creator_notes": str(sheet.get("creator_notes", "") or ""),
        "system_prompt": build_chat_system(sheet),
        "post_history_instructions": "",
        "alternate_greetings": list(sheet.get("alternate_greetings", []) or []),
        "tags": [],
        "creator": "CharacterCreator",
        "character_version": "1.0",
        # Free-form zone per spec: stash the exact sheet so our own cards
        # round-trip losslessly (description is a merged view otherwise).
        "extensions": {"character_creator": {"sheet": sheet}},
    }
    return {"spec": CARD_SPEC, "spec_version": CARD_SPEC_VERSION, "data": data}


def card_data_to_sheet(data: Dict[str, Any]) -> Dict[str, Any]:
    """Convert v2 card `data` back to a sheet. Own cards restore exactly via
    extensions.character_creator.sheet; foreign cards map best-effort."""
    ext = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    inner = ext.get("character_creator") if isinstance(ext, dict) else None
    inner_sheet = inner.get("sheet") if isinstance(inner, dict) else None
    if isinstance(inner_sheet, dict) and str(inner_sheet.get("name", "")).strip():
        return normalize_sheet(inner_sheet)
    description = str(data.get("description", "") or "").strip()
    return normalize_sheet({
        "name": str(data.get("name", "Unnamed") or "Unnamed"),
        "appearance": description,
        "personality": str(data.get("personality", "") or ""),
        "backstory": str(data.get("scenario", "") or data.get("creator_notes", "") or ""),
        "abilities": [],
        "weaknesses": [],
        "goals": [],
        "catchphrases": [],
        "relationships": [],
        "stats": {},
        "first_mes": str(data.get("first_mes", "") or ""),
        "alternate_greetings": data.get("alternate_greetings", []),
        "mes_example": str(data.get("mes_example", "") or ""),
        "scenario": str(data.get("scenario", "") or ""),
        "creator_notes": str(data.get("creator_notes", "") or ""),
    })


def make_card_png(card: Dict[str, Any]) -> bytes:
    """Render a placeholder portrait and embed the card JSON in a `chara`
    tEXt chunk (v2 string form). Pillow-only, stdlib font."""
    from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

    payload = json.dumps(card, ensure_ascii=False)
    img = Image.new("RGB", (512, 768), (201, 168, 120))  # kraft dossier cover
    draw = ImageDraw.Draw(img)
    draw.rectangle([24, 24, 488, 744], outline=(43, 33, 24), width=4)
    draw.rectangle([40, 40, 472, 728], fill=(244, 234, 211), outline=(43, 33, 24), width=2)
    name = str((card.get("data") or {}).get("name", "?") or "?")
    initial = name.strip()[0].upper()
    font_big = ImageFont.load_default(size=160)
    font_small = ImageFont.load_default(size=28)
    draw.text((256, 300), initial, fill=(43, 33, 24), font=font_big, anchor="mm")
    draw.text((256, 470), name[:24], fill=(90, 76, 58), font=font_small, anchor="mm")
    draw.text((256, 510), "CASTING DOSSIER · 8012", fill=(163, 53, 43), font=font_small, anchor="mm")
    info = PngImagePlugin.PngInfo()
    info.add_text("chara", payload)
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def read_chara_chunk(png_bytes: bytes) -> Dict[str, Any]:
    """Extract the card JSON from a PNG's `chara` chunk (v2 string, with a
    best-effort v3 base64 fallback)."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(png_bytes))
        img.load()
        raw = img.info.get("chara")
    except Exception as exc:
        raise ValueError(f"Could not read PNG: {exc}") from exc
    if not raw:
        raise ValueError("PNG has no embedded character card (`chara` chunk missing).")
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass
    try:
        obj = json.loads(base64.b64decode(raw).decode("utf-8"))
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    raise ValueError("Embedded character card is not valid JSON.")


def coerce_card_payload(obj: Any) -> Dict[str, Any]:
    """Accept a full card ({spec, data}) or a bare v2 `data` dict."""
    if not isinstance(obj, dict):
        raise ValueError("Character card must be a JSON object.")
    data = obj.get("data") if isinstance(obj.get("data"), dict) else obj
    if not isinstance(data, dict) or not str(data.get("name", "")).strip():
        raise ValueError("Not a valid character card (missing character name).")
    return data


# ---------- request models ----------
class KeyIn(BaseModel):
    key: str

    @field_validator("key")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("API key is required.")
        if len(v) > 300:
            raise ValueError("API key looks invalid (too long).")
        return v


class CreateIn(BaseModel):
    concept: str = ""
    world: str = ""
    personality: str = ""
    powers: str = ""
    role: str = ""
    genre: str = ""

    @field_validator("concept")
    @classmethod
    def _concept(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Concept is required — describe your character idea.")
        if len(v) > 500:
            raise ValueError("Concept must be 500 characters or less.")
        return v

    @field_validator("world", "personality", "powers", "role", "genre")
    @classmethod
    def _opt(cls, v: str) -> str:
        v = (v or "").strip()
        if len(v) > 500:
            raise ValueError("Optional fields must be 500 characters or less.")
        return v


class ModifyIn(BaseModel):
    instruction: str

    @field_validator("instruction")
    @classmethod
    def _ins(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Modification instruction is required.")
        if len(v) > 1000:
            raise ValueError("Instruction must be 1000 characters or less.")
        return v


class ChatIn(BaseModel):
    message: str

    @field_validator("message")
    @classmethod
    def _msg(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Message cannot be empty.")
        if len(v) > 1000:
            raise ValueError("Message must be 1000 characters or less.")
        return v


# ---------- routes ----------
@app.get("/api/status")
def api_status():
    return {"has_key": bool(effective_key()), "model": MODEL, "has_character": _current_character is not None}


@app.post("/api/key")
def api_set_key(body: KeyIn):
    global _runtime_key
    candidate = body.key.strip()
    try:
        client = make_client(candidate)
        client.models.list()  # live verify
    except Exception as exc:  # noqa: BLE001
        return groq_error_response(exc)
    _runtime_key = candidate
    return {"ok": True, "message": "API key verified and stored for this server session."}


@app.delete("/api/key")
def api_delete_key():
    global _runtime_key
    _runtime_key = None
    return {"ok": True, "has_key": bool(effective_key())}


@app.get("/api/character")
def api_get_character():
    if not _current_character:
        return JSONResponse(status_code=404, content={"error": "No character yet. Create one first."})
    return {"character": _current_character}


@app.post("/api/create")
def api_create(body: CreateIn):
    global _current_character, _chat_history
    key = effective_key()
    if not key:
        return JSONResponse(status_code=401, content={"error": "No API key. Open Settings and add your Groq key."})
    try:
        client = make_client(key)
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": CREATE_SYSTEM},
                {"role": "user", "content": build_create_prompt(body.model_dump())},
            ],
            temperature=0.9,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
        text = (resp.choices[0].message.content or "").strip()
        sheet = normalize_sheet(parse_json_defensive(text))
    except ValueError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return groq_error_response(exc)
    _current_character = sheet
    _chat_history = []
    return {"character": sheet}


@app.post("/api/modify")
def api_modify(body: ModifyIn):
    global _current_character
    if not _current_character:
        return JSONResponse(status_code=400, content={"error": "No character yet. Create one first."})
    key = effective_key()
    if not key:
        return JSONResponse(status_code=401, content={"error": "No API key. Open Settings and add your Groq key."})
    try:
        client = make_client(key)
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": CREATE_SYSTEM + "\nYou are updating an existing character. Apply the user's change, keep everything else consistent. Return the FULL updated sheet as JSON."},
                {"role": "user", "content": f"Current sheet:\n{json.dumps(_current_character)}\n\nChange request: {body.instruction}\nReturn the full updated sheet as JSON."},
            ],
            temperature=0.8,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )
        text = (resp.choices[0].message.content or "").strip()
        _current_character = normalize_sheet(parse_json_defensive(text))
    except ValueError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return groq_error_response(exc)
    return {"character": _current_character}


@app.post("/api/chat")
def api_chat(body: ChatIn):
    global _chat_history
    if not _current_character:
        return JSONResponse(status_code=400, content={"error": "No character yet. Create one first, then chat."})
    key = effective_key()
    if not key:
        return JSONResponse(status_code=401, content={"error": "No API key. Open Settings and add your Groq key."})
    try:
        client = make_client(key)
        messages = [{"role": "system", "content": build_chat_system(_current_character)}]
        messages += _chat_history[-MAX_HISTORY_MESSAGES:]
        messages.append({"role": "user", "content": body.message})
        resp = client.chat.completions.create(model=MODEL, messages=messages, temperature=0.85, max_tokens=600)
        reply = (resp.choices[0].message.content or "").strip() or "..."
    except Exception as exc:  # noqa: BLE001
        return groq_error_response(exc)
    _chat_history.append({"role": "user", "content": body.message})
    _chat_history.append({"role": "assistant", "content": reply})
    _chat_history = _chat_history[-MAX_HISTORY_MESSAGES:]
    return {"reply": reply, "character_name": _current_character.get("name", "")}


@app.post("/api/reset")
def api_reset():
    global _chat_history
    _chat_history = []
    return {"ok": True}


@app.get("/api/export")
def api_export():
    if not _current_character:
        return JSONResponse(status_code=404, content={"error": "No character yet. Create one first."})
    md = build_markdown(_current_character)
    fname = re.sub(r"[^a-z0-9]+", "-", (_current_character.get("name", "character") or "character").lower()).strip("-") or "character"
    return {"markdown": md, "filename": f"{fname}.md"}


@app.get("/api/card/export")
def api_card_export(format: str = "json"):
    """Export the current character as a SillyTavern chara_card v2 payload:
    ?format=json (default) or ?format=png (card embedded in `chara` chunk)."""
    if not _current_character:
        return JSONResponse(status_code=404, content={"error": "No character yet. Create one first."})
    fmt = (format or "json").strip().lower()
    card = sheet_to_card(_current_character)
    fname = slugify(_current_character.get("name", "character"))
    if fmt == "json":
        return JSONResponse(
            content=card,
            headers={"Content-Disposition": f'attachment; filename="{fname}.json"'},
        )
    if fmt == "png":
        try:
            blob = make_card_png(card)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(status_code=502, content={"error": f"Could not render card PNG. ({str(exc)[:120]})"})
        return Response(
            content=blob,
            media_type="image/png",
            headers={"Content-Disposition": f'attachment; filename="{fname}.png"'},
        )
    return JSONResponse(status_code=400, content={"error": "Unknown format. Use ?format=json or ?format=png."})


@app.post("/api/card/import")
async def api_card_import(file: UploadFile = File(...)):
    """Import a character card (.json card/data, or .png with `chara` chunk)
    into the session character. Additive: chat history resets."""
    global _current_character, _chat_history
    blob = await file.read()
    if not blob:
        return JSONResponse(status_code=400, content={"error": "Uploaded file is empty."})
    if len(blob) > CARD_UPLOAD_LIMIT:
        return JSONResponse(status_code=400, content={"error": "File too large (10 MB limit)."})
    fname = (file.filename or "").lower()
    try:
        if fname.endswith(".png") or blob[:8] == PNG_MAGIC:
            data = coerce_card_payload(read_chara_chunk(blob))
        else:
            try:
                text = blob.decode("utf-8")
            except UnicodeDecodeError:
                return JSONResponse(status_code=400, content={"error": "Could not read file. Send a .json card or a .png card."})
            try:
                data = coerce_card_payload(json.loads(text))
            except json.JSONDecodeError:
                return JSONResponse(status_code=400, content={"error": "Invalid JSON. Send a character card (.json) or card PNG."})
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    _current_character = card_data_to_sheet(data)
    _chat_history = []
    return {"character": _current_character, "card_name": data.get("name", "")}


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
