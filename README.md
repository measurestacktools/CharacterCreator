# AI Character Creator

Create **your** character from a spark of an idea, then chat with them in-character.

## Features
- Guided creation (concept, world, personality, powers, role, genre)
- Animated character card: stats bars, backstory, catchphrases, relationships
- In-character chat that keeps personality/background, regenerate, modify, export `.md`, reset

## Requirements
- Python 3.10+
- A free Groq API key ([console.groq.com/keys](https://console.groq.com/keys))
- Internet (AI calls go to Groq)

## Installation
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate | macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env   # add GROQ_API_KEY  (or paste the key in Settings later)
```

## How to use
1. Describe your concept → **Create** → meet your character card
2. **Chat** with them, **Modify** traits, **Export** the sheet

## Limitations
- Single in-memory character + chat history (restart clears it)
- Needs a Groq key + internet; chat history capped at recent turns

- Stack: Python + FastAPI + vanilla HTML/CSS/JS, port **8012**
- LLM: OpenAI SDK → `https://api.groq.com/openai/v1`, model `openai/gpt-oss-120b`
- Key: Settings modal → backend live-verifies (`models.list`), process-global memory only (`DELETE /api/key`); `.env` fallback; `/api/status` presence only; frontend never stores/sends keys.

## Run
```powershell
pip install -r requirements.txt
uvicorn app:app --port 8012
# open http://127.0.0.1:8012
```

## Troubleshooting
- `No API key` — open Settings (⚙) and paste a Groq key, or set `GROQ_API_KEY` in `.env`.
- `Invalid API key` — the key is rejected by Groq; generate a fresh one at console.groq.com/keys.
- `Groq rate limit hit` — free-tier pacing; wait ~30s and retry.
- `AI returned malformed data` — retry Create/Modify; the sheet normalizer keeps stats 1–100.
- Port in use — run `uvicorn app:app --port 8012` on a free port.

## Mature mode (18+)
- Optional toggle on the creation panel ("Mature (18+)" + "I confirm I am 18 or older"). Session-only server flag; cleared on restart, never persisted (no localStorage).
- `POST /api/mode {"mature":bool,"confirm18":bool}` — enabling requires `confirm18:true`, else `400`. Disabling needs no confirm. `GET /api/status` reports `mature`.
- When on, creation/chat/modify use a mature system-prompt variant allowing: graphic horror/violence descriptions, crime anti-heroes, moral ambiguity, profanity, adult dark-romance themes (non-explicit, fade-to-black).
- Hard blocks stay active in BOTH modes and take precedence: explicit sexual content/detail, ANY sexual content involving minors, non-consensual sexual scenarios, real-person sexual content. Blocked inputs get a friendly in-fiction redirect toward non-explicit drama, never a lecture.

## Test
```powershell
pip install -r requirements-test.txt
pytest -q
```

## API
- `GET /api/status` — key presence, model, character presence
- `POST /api/key {key}` / `DELETE /api/key`
- `POST /api/create {concept*, world, personality, powers, role, genre}` → full sheet
- `POST /api/modify {instruction}` → updated sheet
- `POST /api/chat {message}` — in-character reply (needs sheet)
- `POST /api/reset` — clear conversation
- `GET /api/character`, `GET /api/export` — markdown download
- `GET /api/portrait` — AI portrait for the current sheet (lazy, cached)

## Character portraits
- Portraits are generated free via [Pollinations.ai](https://image.pollinations.ai) — no key needed.
- Prompt is built short from the sheet (`portrait of <name>, <appearance>, setting: <scenario>, … dossier photo style`); seed is a stable sha256 hash of the character name, so the same character always gets the same face.
- Image bytes are cached in server memory only (cleared on restart / new character / import); any fetch failure (timeout 45s, non-200, empty) returns a clean `404` JSON and the card falls back to the name initial.
