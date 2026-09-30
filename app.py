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

import json
import os
import re
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
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
    norm = {
        "name": str(raw.get("name", "Unnamed")).strip()[:80] or "Unnamed",
        "appearance": str(raw.get("appearance", "")).strip()[:1500] or "Mysterious figure.",
        "personality": str(raw.get("personality", "")).strip()[:1500] or "Complex and intriguing.",
        "backstory": str(raw.get("backstory", "")).strip()[:3000] or "Origins unknown.",
        "abilities": as_str_list(raw.get("abilities")),
        "weaknesses": as_str_list(raw.get("weaknesses")),
        "goals": as_str_list(raw.get("goals")),
        "catchphrases": as_str_list(raw.get("catchphrases")),
        "relationships": as_str_list(raw.get("relationships")),
        "stats": {k: clamp_stat((stats or {}).get(k, 50)) for k in STAT_KEYS},
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
    '"agility": 1-100, "luck": 1-100, "magic": 1-100}}\n'
    "Make the character feel unique and personal, grounded in the user's concept. "
    "2-5 items per list. Stats must be integers 1-100."
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


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
