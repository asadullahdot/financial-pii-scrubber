"""Database tests.  Need PostgreSQL running (docker compose up -d); skipped otherwise."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text                         # noqa: E402
from app import db                                  # noqa: E402
from app.engine import scrub_files, combine         # noqa: E402

try:
    with db.engine.connect() as c:
        c.execute(text("SELECT 1"))
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = pytest.mark.skipif(not DB_UP, reason="PostgreSQL not reachable")


@pytest.fixture
def run_id():
    if not (ROOT / "data").is_dir():
        pytest.skip("no data/ folder: copy the statements into data/ to run this test")
    db.init_db()
    files = scrub_files(ROOT / "data")
    rid = db.save_run(files)
    yield rid
    with db.engine.begin() as c:                    # clean up; files cascade-delete
        c.execute(text("DELETE FROM scrub_runs WHERE id = :i"), {"i": rid})


def test_roundtrip_preserves_combined_string(run_id):
    stored = db.get_run(run_id)
    assert stored["combined_text"] == combine(scrub_files(ROOT / "data"))
    assert stored["file_count"] == len(stored["files"]) > 0


def test_one_row_per_file_with_status(run_id):
    names = {f["filename"] for f in db.get_run(run_id)["files"]}
    assert {p.name for p in (ROOT / "data").iterdir() if p.is_file()} == names


def test_stored_text_has_no_obvious_pii(run_id):
    import re
    text_ = db.get_run(run_id)["combined_text"]
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text_)       # no emails
    assert not re.search(r"(?<![\d.,])\d{8,}(?![\d]|[.,]\d)", text_)  # no long digit runs


def test_failed_save_rolls_back_everything():
    from app.engine import ScrubbedFile
    db.init_db()
    before = len(db.list_runs(1000))
    bad = [ScrubbedFile("a.txt", ".txt", "ok", "x", {}),
           ScrubbedFile("a.txt", ".txt", "ok", "y", {})]   # duplicate name violates UNIQUE
    with pytest.raises(Exception):
        db.save_run(bad)
    assert len(db.list_runs(1000)) == before               # no half-saved run


def test_get_missing_run_returns_none():
    db.init_db()
    assert db.get_run(-1) is None
