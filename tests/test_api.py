"""API tests (need PostgreSQL running; skipped otherwise).  pytest -v"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient      # noqa: E402
from sqlalchemy import text                    # noqa: E402
from app import db                             # noqa: E402
from app.main import app, _safe_filename       # noqa: E402
from app.verifier import find_leaks            # noqa: E402

try:
    with db.engine.connect() as c:
        c.execute(text("SELECT 1"))
    DB_UP = True
except Exception:
    DB_UP = False

pytestmark = pytest.mark.skipif(not DB_UP, reason="PostgreSQL not reachable")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def cleanup():
    ids = []
    yield ids
    for i in ids:
        db.delete_run(i)


def _upload_all_data(client):
    files = [("files", (p.name, p.read_bytes())) for p in sorted((ROOT / "data").iterdir()) if p.is_file()]
    return client.post("/runs", files=files)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "database": "up"}


def test_upload_scrubs_stores_and_returns_one_string(client, cleanup):
    r = _upload_all_data(client)
    assert r.status_code == 201
    body = r.json(); cleanup.append(body["run_id"])
    assert body["file_count"] == 4 and all(f["status"] == "ok" for f in body["files"])
    for f in body["files"]:
        assert f"---- File: {f['filename']} ----" in body["combined_text"]
    assert find_leaks(body["combined_text"]) == {}


def test_response_and_db_never_contain_raw_identifiers(client, cleanup):
    body = _upload_all_data(client).json(); cleanup.append(body["run_id"])
    stored = db.get_run(body["run_id"])["combined_text"]
    for raw in ("bilal.hkhan91@gmail.com", "PK36SCBL0000001123456702", "42201-7654321-3", "0300-1234567"):
        assert raw not in body["combined_text"] and raw not in stored


def test_get_run_list_and_download(client, cleanup):
    rid = _upload_all_data(client).json()["run_id"]; cleanup.append(rid)
    assert client.get(f"/runs/{rid}").json()["run_id"] == rid
    assert any(r["run_id"] == rid for r in client.get("/runs").json())
    dl = client.get(f"/runs/{rid}/combined")
    assert dl.status_code == 200 and "attachment" in dl.headers["content-disposition"]
    assert dl.text == client.get(f"/runs/{rid}").json()["combined_text"]


def test_from_folder_default_and_missing(client, cleanup):
    r = client.post("/runs/from-folder", json={})
    assert r.status_code == 201; cleanup.append(r.json()["run_id"])
    assert client.post("/runs/from-folder", json={"folder": "no_such_folder"}).status_code == 404


def test_unsupported_file_is_skipped_not_fatal(client, cleanup):
    r = client.post("/runs", files=[("files", ("pic.png", b"\x89PNG")), ("files", ("ok.txt", b"Paid to Netflix.com 3.99"))])
    assert r.status_code == 201; cleanup.append(r.json()["run_id"])
    status = {f["filename"]: f["status"] for f in r.json()["files"]}
    assert status == {"pic.png": "skipped", "ok.txt": "ok"}


def test_unknown_run_is_404(client):
    assert client.get("/runs/999999999").status_code == 404
    assert client.delete("/runs/999999999").status_code == 404


def test_delete_cascades(client):
    rid = _upload_all_data(client).json()["run_id"]
    assert client.delete(f"/runs/{rid}").status_code == 204
    with db.engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM scrubbed_files WHERE run_id = :i"), {"i": rid}).scalar()
    assert n == 0


def test_verifier_gate_blocks_and_stores_nothing(client, monkeypatch):
    """If the scrubber ever failed, the gate must stop it BEFORE the database."""
    import app.engine as engine
    monkeypatch.setattr(engine, "scrub_text", lambda t: t)     # simulate a scrubber that does nothing
    before = len(db.list_runs(1000))
    r = client.post("/runs", files=[("files", ("leak.txt", b"Email: jane.doe@example.com Acct 12345678901"))])
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "email" in detail["findings"] and "long digits" in detail["findings"]
    assert "jane.doe" not in r.text                            # the leaked value is never echoed back
    assert len(db.list_runs(1000)) == before                   # nothing stored


@pytest.mark.parametrize("raw,expected", [
    ("../../etc/passwd.txt", "passwd.txt"),
    ("C:\\Users\\me\\stmt.pdf", "stmt.pdf"),
    ("we ird*name?.csv", "we ird_name_.csv"),
    (None, "upload"),
])
def test_filename_sanitising(raw, expected):
    assert _safe_filename(raw, set()) == expected


def test_duplicate_filenames_made_unique():
    taken = set()
    assert [_safe_filename("a.txt", taken) for _ in range(3)] == ["a.txt", "a_2.txt", "a_3.txt"]


def test_too_many_files(client):
    files = [("files", (f"f{i}.txt", b"x")) for i in range(51)]
    assert client.post("/runs", files=files).status_code == 413
