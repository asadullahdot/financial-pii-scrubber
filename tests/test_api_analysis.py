"""Analysis endpoints against the real database, with Groq faked.  Skipped without PostgreSQL."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fastapi.testclient import TestClient      # noqa: E402
from sqlalchemy import text                    # noqa: E402
from app import analysis, db                   # noqa: E402
from app.main import app                       # noqa: E402
from test_analysis import FakeClient, STATEMENT  # noqa: E402

try:
    with db.engine.connect() as c:
        c.execute(text("SELECT 1"))
except Exception:
    pytest.skip("PostgreSQL not reachable", allow_module_level=True)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def run_id(client):
    rid = client.post("/runs", files=[("files", ("stmt.txt", STATEMENT.encode()))]).json()["run_id"]
    yield rid
    db.delete_run(rid)


def test_analysis_roundtrip(client, run_id, monkeypatch):
    fake = FakeClient(insights=["You spent PKR 40,000.00 on cash withdrawals."])
    monkeypatch.setattr(analysis, "get_client", lambda: fake)
    assert client.get(f"/runs/{run_id}/analysis").status_code == 404          # none yet
    r = client.post(f"/runs/{run_id}/analysis")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["model"] == analysis.MODEL and body["result"]["by_currency"]["PKR"]["total_out"] == 41111.02
    assert body["validation"]["kept"] == 5 and body["result"]["insights"]
    assert client.get(f"/runs/{run_id}/analysis").json()["analysis_id"] == body["analysis_id"]


def test_only_scrubbed_text_is_sent_to_the_model(client, run_id, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(analysis, "get_client", lambda: fake)
    client.post(f"/runs/{run_id}/analysis")
    sent = " ".join(m["content"] for req in fake.requests for m in req["messages"])
    assert "<REF_ID>" in sent and "Bilal Hassan Khan" in sent                 # names are kept on purpose
    stored = db.get_run(run_id)["files"][0]["scrubbed_text"]
    assert stored.splitlines()[2] in sent                                     # exactly the stored (scrubbed) lines


def test_gate_blocks_leaky_stored_text(client, run_id, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(analysis, "get_client", lambda: fake)
    with db.engine.begin() as c:                                              # simulate a leak in storage
        c.execute(text("UPDATE scrubbed_files SET scrubbed_text = scrubbed_text || ' jane.doe@example.com'"
                       " WHERE run_id = :i"), {"i": run_id})
    r = client.post(f"/runs/{run_id}/analysis")
    assert r.status_code == 422 and "NOTHING was sent" in r.json()["detail"]["message"]
    assert fake.requests == [] and "jane.doe" not in r.text


def test_missing_api_key_is_a_clear_503(client, run_id, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    r = client.post(f"/runs/{run_id}/analysis")
    assert r.status_code == 503 and "GROQ_API_KEY" in r.json()["detail"]


def test_unknown_run_404(client):
    assert client.post("/runs/999999999/analysis").status_code == 404
