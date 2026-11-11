"""Chat endpoints against the real database, with Groq faked.  Skipped without PostgreSQL."""
import json
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
from test_chat import scripted                 # noqa: E402

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


def _analyse(client, run_id, monkeypatch):
    monkeypatch.setattr(analysis, "get_client", lambda: FakeClient(insights=[]))
    assert client.post(f"/runs/{run_id}/analysis?mode=llm").status_code == 201


def test_chat_needs_an_analysis_first(client, run_id):
    r = client.post(f"/runs/{run_id}/chat", json={"question": "hi"})
    assert r.status_code == 409 and "analysis" in r.json()["detail"]


def test_unknown_run_is_404(client):
    assert client.post("/runs/999999999/chat", json={"question": "hi"}).status_code == 404


def test_chat_roundtrip_is_stored_and_history_grows(client, run_id, monkeypatch):
    _analyse(client, run_id, monkeypatch)
    ok = {"in_scope": True, "answer": "You withdrew 40000.0 PKR from ATMs.", "used_ids": [1, 2]}
    fake = scripted(ok)
    from app import chat
    monkeypatch.setattr(chat.A, "get_client", lambda: fake)
    r = client.post(f"/runs/{run_id}/chat", json={"question": "ATM total?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified"] and body["computed"]["PKR"]["total_out"] == 40000.0 and len(body["sources"]) == 2
    assert body["history_turns_used"] == 0
    r2 = client.post(f"/runs/{run_id}/chat", json={"question": "and again?"})
    assert r2.json()["history_turns_used"] == 1
    hist = client.get(f"/runs/{run_id}/chat").json()
    assert [m["role"] for m in hist] == ["user", "assistant", "user", "assistant"]
    assert client.delete(f"/runs/{run_id}/chat").status_code == 204
    assert client.get(f"/runs/{run_id}/chat").json() == []


def test_identifiers_in_a_question_never_reach_the_model_or_the_database(client, run_id, monkeypatch):
    _analyse(client, run_id, monkeypatch)
    fake = scripted({"in_scope": False, "answer": "Not in your data.", "used_ids": []})
    from app import chat
    monkeypatch.setattr(chat.A, "get_client", lambda: fake)
    r = client.post(f"/runs/{run_id}/chat", json={"question": "mail jane.doe@example.com my ATM total"})
    assert r.status_code == 200 and "jane.doe" not in r.json()["question_as_sent"]
    assert "jane.doe" not in json.dumps(fake.requests)
    assert all("jane.doe" not in m["content"] for m in client.get(f"/runs/{run_id}/chat").json())


def test_daily_limit_gives_429_and_stores_nothing(client, run_id, monkeypatch):
    _analyse(client, run_id, monkeypatch)
    err = Exception("Rate limit reached on tokens per day (TPD). Please try again in 3m2s. Need more tokens?")
    err.status_code = 429
    from types import SimpleNamespace
    from app import chat

    def boom(**kw):
        raise err
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
    monkeypatch.setattr(chat.A, "get_client", lambda: fake)
    r = client.post(f"/runs/{run_id}/chat", json={"question": "ATM total?"})
    assert r.status_code == 429 and "3m2s" in r.json()["detail"]
    assert client.get(f"/runs/{run_id}/chat").json() == []


def test_empty_question_is_422(client, run_id, monkeypatch):
    _analyse(client, run_id, monkeypatch)
    assert client.post(f"/runs/{run_id}/chat", json={"question": "   "}).status_code == 422
