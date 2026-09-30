# AI Character Creator

Create **your** character from a spark of an idea, then chat with them in-character.

- Stack: Python + FastAPI + vanilla HTML/CSS/JS, port **8012**
- LLM: OpenAI SDK → `https://api.groq.com/openai/v1`, model `openai/gpt-oss-120b`
- Key: Settings modal → backend live-verifies (`models.list`), process-global memory only (`DELETE /api/key`); `.env` fallback; `/api/status` presence only; frontend never stores/sends keys.

## Run
```powershell
pip install -r requirements.txt
uvicorn app:app --port 8012
# open http://127.0.0.1:8012
```

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
