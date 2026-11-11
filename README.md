# Financial Statement PII Scrubber

Scrubs bank and wallet statements (PDF, DOCX, CSV, TXT, XLSX) **locally**, stores only the scrubbed text in PostgreSQL, then analyses it with Groq and lets you chat about it. Raw statements never leave the machine and are never written to the database.

GitHub: `https://github.com/asadullahdot/financial-pii-scrubber`

## Approach (and limits)

The scrubber is ordered regular expressions plus validators (Luhn, IBAN length, ISO country codes), running from specific to generic rules, so it needs no model, no download and no network. Identifiers become placeholders (`<IBAN>`, `<PHONE>`, ...) while names, merchants (even `Netflix.com`), dates, amounts and masked last-4 digits are kept. The scrubbed text is checked again by an independent leak scanner before it is stored or sent anywhere. Analysis is hybrid: a local parser reads the clear transaction lines, Groq reads only what the parser cannot, every row is verified against the statement text, and all totals are computed in Python, never by the model. The chat answers from the verified transactions, and every number in an answer is checked before it is shown.

Known limits: scanned (image-only) PDFs are not supported (no OCR); an address written inline with no comma or line break (`12 Baker Street London`) can be missed; city names are kept; a phone or ID that follows a numeric CSV cell is not caught; person names are never removed, by design. Chat answers are checked for consistency with the rows the model cites, not for whether it cited all the relevant rows.

## Requirements

- Python 3.11+ (developed on 3.14), Docker Desktop (for PostgreSQL), a free Groq API key from console.groq.com.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env        # then put your GROQ_API_KEY in .env
```

macOS/Linux: `source .venv/bin/activate` instead of `.\.venv\Scripts\`, and `cp` instead of `copy`.

## Database

```powershell
docker compose up -d          # PostgreSQL 16 in container "pii-postgres", port 5432
```

The tables (`db/schema.sql`) are created automatically when the API starts. To create them by hand: `.\.venv\Scripts\python -m app.db`. `.\.venv\Scripts\python check_setup.py` confirms the key, database and libraries.

## Run the API and the frontend

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --reload
```

- Frontend: http://127.0.0.1:8000/ (plain HTML, CSS and JavaScript, served by the same app)
- API docs: http://127.0.0.1:8000/docs

In the frontend: choose files (or press **Scrub the `data/` folder**) -> **Analyse** -> ask questions in the chat. Put the statements to scrub in `data/` (kept out of Git).

Command line (no API): `.\.venv\Scripts\python -m app.engine` scrubs `data/` and writes `output/combined_scrubbed.txt`.

Look at the stored rows:

```powershell
docker exec -it pii-postgres psql -U pii -d pii_scrubber -c "SELECT id, created_at, file_count FROM scrub_runs ORDER BY id DESC LIMIT 5;"
```

## API

| Endpoint | Purpose |
|---|---|
| `POST /runs` | upload files -> scrub -> verify -> store; returns the run |
| `POST /runs/from-folder` | same for a server-side folder (`{"folder": "data"}`) |
| `GET /runs`, `GET /runs/{id}`, `DELETE /runs/{id}` | list, read, delete a run |
| `GET /runs/{id}/combined` | the single combined scrubbed string (download) |
| `POST /runs/{id}/analysis?mode=hybrid\|llm\|local` | analyse (default hybrid) |
| `GET /runs/{id}/analysis` | latest stored analysis |
| `POST /runs/{id}/chat`, `GET`, `DELETE` | ask a question, read or clear the history |

If the scrubbed text still looks like it holds an identifier, the API answers 422 and stores nothing. If Groq's daily quota is used up, a model-only analysis or a chat returns 429 with the wait time; the default hybrid analysis still completes locally.

## Tests

```powershell
.\.venv\Scripts\python -m pytest -q
```

About 540 tests, no network or Groq key needed. The tests that use PostgreSQL skip when it is not running. Groq is replaced by a scripted fake client, so the guardrail tests are repeatable. `tests/test_unseen.py` scrubs nine invented statements from countries that are not in `data/` and checks that every planted identifier is gone and every merchant, name and amount survives. `.\.venv\Scripts\python tests\review.py` prints what is left in the scrubbed `data/` for a manual look.

## Layout

```
app/readers.py     file -> text           app/localparse.py  deterministic transaction parser
app/scrubber.py    the scrubbing rules    app/analysis.py    hybrid analysis + verification
app/engine.py      folder -> one string   app/chat.py        grounded chat
app/verifier.py    independent leak scan  app/main.py        FastAPI app
app/db.py          PostgreSQL storage     frontend/          the web page
db/schema.sql      table definitions      tests/             unit, API and generalisation tests
```
