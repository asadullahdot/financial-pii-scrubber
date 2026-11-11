"""Step 5: PostgreSQL storage.

Only SCRUBBED text is ever written here.  This module has no access to raw
text: it receives ScrubbedFile objects produced by engine.py.

    init_db()                    create the tables (safe to call repeatedly)
    save_run(files)              store one scrub run + its files -> run_id
    get_run(run_id)              fetch a run with its per-file rows
    list_runs()                  newest runs first (no big text columns)
    save_analysis(...)           store a Groq analysis for a run
    get_latest_analysis(run_id)  newest analysis of a run, or None
    add_chat_message / get_chat / clear_chat   chat turns of a run
    delete_run(run_id)           remove a run and everything attached to it

Run `python -m app.db` once to create the tables.
"""
import json
import os
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from app.engine import ScrubbedFile, combine

load_dotenv()  # reads DATABASE_URL from the .env file in the project root

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+psycopg://pii:pii_pass@localhost:5432/pii_scrubber"
)
SCHEMA_FILE = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

# One shared connection pool for the whole app.  pool_pre_ping drops dead
# connections (e.g. after the Docker container restarts) instead of erroring.
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def init_db() -> None:
    """Create all tables by running db/schema.sql (idempotent)."""
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    # exec_driver_sql sends the file as-is, so it may hold several statements.
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)


def save_run(files: list[ScrubbedFile]) -> int:
    """Store one run and all its files in ONE transaction; return the run id.

    The transaction means a run is saved completely or not at all: you can
    never end up with a run row that is missing some of its files.
    """
    combined = combine(files)
    totals = Counter()
    for f in files:
        totals.update(f.placeholder_counts)

    with engine.begin() as conn:  # commits on success, rolls back on any error
        run_id = conn.execute(
            text("""INSERT INTO scrub_runs (file_count, combined_text, placeholder_counts)
                    VALUES (:n, :combined, CAST(:counts AS jsonb)) RETURNING id"""),
            {"n": len(files), "combined": combined, "counts": json.dumps(totals)},
        ).scalar_one()

        for f in files:
            conn.execute(
                text("""INSERT INTO scrubbed_files
                            (run_id, filename, file_type, status, scrubbed_text, placeholder_counts)
                        VALUES (:run_id, :name, :ftype, :status, :txt, CAST(:counts AS jsonb))"""),
                {"run_id": run_id, "name": f.name, "ftype": f.file_type, "status": f.status,
                 "txt": f.text, "counts": json.dumps(f.placeholder_counts)},
            )
    return run_id


def get_run(run_id: int) -> dict | None:
    """Return the run (with combined_text) and its files, or None if not found."""
    with engine.connect() as conn:
        run = conn.execute(
            text("SELECT id, created_at, file_count, combined_text, placeholder_counts "
                 "FROM scrub_runs WHERE id = :id"), {"id": run_id}).mappings().first()
        if run is None:
            return None
        files = conn.execute(
            text("SELECT filename, file_type, status, scrubbed_text, placeholder_counts "
                 "FROM scrubbed_files WHERE run_id = :id ORDER BY filename"),
            {"id": run_id}).mappings().all()
    return {**run, "files": [dict(f) for f in files]}


def list_runs(limit: int = 20) -> list[dict]:
    """Newest runs first.  Omits the large text columns on purpose."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, created_at, file_count, placeholder_counts "
                 "FROM scrub_runs ORDER BY id DESC LIMIT :limit"), {"limit": limit}).mappings().all()
    return [dict(r) for r in rows]


def save_analysis(run_id: int, model: str, prompt_version: str, result: dict, validation: dict) -> int:
    """Store one analysis (JSONB) for a run; several per run are allowed."""
    with engine.begin() as conn:
        return conn.execute(
            text("""INSERT INTO analyses (run_id, model, prompt_version, result, validation)
                    VALUES (:run_id, :model, :pv, CAST(:result AS jsonb), CAST(:val AS jsonb))
                    RETURNING id"""),
            {"run_id": run_id, "model": model, "pv": prompt_version,
             "result": json.dumps(result), "val": json.dumps(validation)}).scalar_one()


def get_latest_analysis(run_id: int) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("""SELECT id, run_id, created_at, model, prompt_version, result, validation
                    FROM analyses WHERE run_id = :id ORDER BY created_at DESC, id DESC LIMIT 1"""),
            {"id": run_id}).mappings().first()
    return dict(row) if row else None


def add_chat_message(run_id: int, role: str, content: str) -> int:
    """Store one chat turn (the user's SCRUBBED question or the verified answer)."""
    with engine.begin() as conn:
        return conn.execute(text("INSERT INTO chat_messages (run_id, role, content) "
                                 "VALUES (:r, :role, :c) RETURNING id"),
                            {"r": run_id, "role": role, "c": content}).scalar_one()


def get_chat(run_id: int) -> list[dict]:
    """All chat turns of a run, oldest first."""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, created_at, role, content FROM chat_messages "
                                 "WHERE run_id = :r ORDER BY id"), {"r": run_id}).mappings().all()
    return [dict(r) for r in rows]


def clear_chat(run_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM chat_messages WHERE run_id = :r"), {"r": run_id})


def delete_run(run_id: int) -> bool:
    """Delete a run; its files, analyses and chat rows go with it (ON DELETE CASCADE)."""
    with engine.begin() as conn:
        result = conn.execute(text("DELETE FROM scrub_runs WHERE id = :id"), {"id": run_id})
    return result.rowcount > 0


if __name__ == "__main__":
    init_db()
    print("Tables created (or already present).")
