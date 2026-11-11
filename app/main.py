"""Step 6: the FastAPI service.

The central resource is a RUN: one "scrub this set of statements" action.
Everything else (files, and later the analysis and chat) hangs off a run id.

    GET    /health                 API + database status
    POST   /runs                   upload files (multipart) -> scrub -> verify -> store
    POST   /runs/from-folder       same, for a folder on the server (e.g. a hidden test folder)
    GET    /runs                   list runs (newest first, no text)
    GET    /runs/{id}              one run: per-file info + the combined scrubbed string
    GET    /runs/{id}/combined     the combined scrubbed string as plain text (download)
    POST   /runs/{id}/analysis     Groq analysis of the scrubbed text (verified, stored)
    GET    /runs/{id}/analysis     latest stored analysis of the run
    DELETE /runs/{id}              remove a run and everything attached to it

Privacy rules enforced here
  1. Uploaded files are written to a temporary folder only so the readers can open
     them; the folder is deleted in a `finally` block, success or failure.
  2. Every result passes the independent verifier gate BEFORE it is stored.
     Fail closed: if anything still looks like PII the request is rejected
     (HTTP 422) and nothing is written to the database.
  3. Responses only contain scrubbed text and category counts, never raw text,
     and never the value of a failed verification.

Run:  uvicorn app.main:app --reload        then open http://127.0.0.1:8000/docs
"""
import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import analysis, db
from app.engine import PROJECT_ROOT, ScrubbedFile, combine, scrub_files
from app.schemas import AnalysisOut, FileOut, FolderRequest, Health, RunDetail, RunSummary
from app.verifier import verify_clean

MAX_FILES = 50
MAX_FILE_BYTES = 25 * 1024 * 1024   # 25 MB per uploaded file
FRONTEND_DIR = PROJECT_ROOT / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the tables on startup so a fresh database just works."""
    try:
        db.init_db()
    except SQLAlchemyError:
        pass  # API still starts; /health will report the database as down
    yield


app = FastAPI(
    title="Financial PII Scrubber",
    description="Scrubs bank/wallet statements locally, stores only the scrubbed text, "
                "then (later steps) analyses it with Groq and answers grounded questions.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------- Swagger fix
def _custom_openapi():
    """Make file uploads show a file picker in every Swagger UI version.

    FastAPI describes an uploaded file as {"type": "string",
    "contentMediaType": "application/octet-stream"} (OpenAPI 3.1 style).  Some
    Swagger UI builds do not recognise that and fall back to a plain text box
    ("Add string item").  The older {"type": "string", "format": "binary"}
    form is understood by all of them, so we rewrite the schema once.
    """
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi
    schema = get_openapi(title=app.title, version=app.version,
                         description=app.description, routes=app.routes)

    def rewrite(node):
        if isinstance(node, dict):
            if node.get("contentMediaType") == "application/octet-stream":
                node.pop("contentMediaType")
                node["format"] = "binary"
            for v in node.values():
                rewrite(v)
        elif isinstance(node, list):
            for v in node:
                rewrite(v)

    rewrite(schema)
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi


# ---------------------------------------------------------------- helpers
def _safe_filename(name: str | None, taken: set[str]) -> str:
    """Make an uploaded file name safe and unique.

    Strips any directory part ("../../x.txt" -> "x.txt"), replaces odd
    characters, and appends _2, _3... if two uploads share a name.
    """
    base = Path((name or "upload").replace("\\", "/")).name
    base = re.sub(r"[^\w.\- ()]", "_", base).strip(" .")[:120] or "upload"
    stem, dot, ext = base.rpartition(".")
    stem, ext = (stem, "." + ext) if dot else (base, "")
    candidate, n = base, 2
    while candidate.lower() in taken:
        candidate, n = f"{stem}_{n}{ext}", n + 1
    taken.add(candidate.lower())
    return candidate


def _run_detail(run: dict) -> RunDetail:
    return RunDetail(
        run_id=run["id"], created_at=run["created_at"], file_count=run["file_count"],
        placeholder_counts=run["placeholder_counts"], combined_text=run["combined_text"],
        files=[FileOut(filename=f["filename"], file_type=f["file_type"], status=f["status"],
                       chars=len(f["scrubbed_text"]), placeholder_counts=f["placeholder_counts"])
               for f in run["files"]],
    )


def _verify_and_store(files: list[ScrubbedFile]) -> RunDetail:
    """The gate, then the database.  Shared by both POST endpoints."""
    if not files:
        raise HTTPException(400, "No files to scrub.")

    report = verify_clean(combine(files))
    if not report.ok:
        # Counts only, never the matched values.
        raise HTTPException(422, detail={
            "message": "Verification failed: the scrubbed output still looks like it contains "
                       "personal identifiers, so it was NOT stored.",
            "findings": report.findings,
        })
    try:
        run_id = db.save_run(files)
        return _run_detail(db.get_run(run_id))
    except SQLAlchemyError:
        raise HTTPException(503, "Database unavailable. Is PostgreSQL running?")


# ---------------------------------------------------------------- endpoints
@app.get("/health", response_model=Health, tags=["system"])
def health():
    try:
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(503, "Database unavailable. Is PostgreSQL running?")
    return Health(status="ok", database="up")


@app.post("/runs", response_model=RunDetail, status_code=201, tags=["runs"])
def create_run_from_upload(files: list[UploadFile] = File(..., description="PDF, DOCX, CSV, TXT, XLSX")):
    """Upload statements, scrub them, verify, store.  Returns the run with the
    combined scrubbed string."""
    if not files:
        raise HTTPException(400, "Upload at least one file.")
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"Too many files (max {MAX_FILES}).")

    tmp = Path(tempfile.mkdtemp(prefix="scrub_"))
    try:
        taken: set[str] = set()
        for up in files:
            data = up.file.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                raise HTTPException(413, f"'{up.filename}' is larger than {MAX_FILE_BYTES // 2**20} MB.")
            (tmp / _safe_filename(up.filename, taken)).write_bytes(data)
        scrubbed = scrub_files(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # raw files never outlive the request
    return _verify_and_store(scrubbed)


@app.post("/runs/from-folder", response_model=RunDetail, status_code=201, tags=["runs"])
def create_run_from_folder(body: FolderRequest = FolderRequest()):
    """Scrub every file in a folder on the machine running the API (default: data/)."""
    folder = Path(body.folder)
    if not folder.is_absolute():
        folder = PROJECT_ROOT / folder
    if not folder.is_dir():
        raise HTTPException(404, f"Folder not found: {folder}")
    return _verify_and_store(scrub_files(folder))


@app.get("/runs", response_model=list[RunSummary], tags=["runs"])
def list_runs(limit: int = 20):
    try:
        rows = db.list_runs(max(1, min(limit, 200)))
    except SQLAlchemyError:
        raise HTTPException(503, "Database unavailable. Is PostgreSQL running?")
    return [RunSummary(run_id=r["id"], created_at=r["created_at"], file_count=r["file_count"],
                       placeholder_counts=r["placeholder_counts"]) for r in rows]


def _get_or_404(run_id: int) -> dict:
    try:
        run = db.get_run(run_id)
    except SQLAlchemyError:
        raise HTTPException(503, "Database unavailable. Is PostgreSQL running?")
    if run is None:
        raise HTTPException(404, f"Run {run_id} not found.")
    return run


@app.get("/runs/{run_id}", response_model=RunDetail, tags=["runs"])
def get_run(run_id: int):
    return _run_detail(_get_or_404(run_id))


@app.get("/runs/{run_id}/combined", response_class=PlainTextResponse, tags=["runs"])
def get_combined_text(run_id: int):
    """The single combined scrubbed string, as a plain-text download."""
    return PlainTextResponse(_get_or_404(run_id)["combined_text"],
                             headers={"Content-Disposition": f'attachment; filename="run_{run_id}_scrubbed.txt"'})


def _analysis_out(row: dict) -> AnalysisOut:
    return AnalysisOut(analysis_id=row["id"], run_id=row["run_id"], created_at=row["created_at"],
                       model=row["model"], prompt_version=row["prompt_version"],
                       result=row["result"], validation=row["validation"])


@app.post("/runs/{run_id}/analysis", response_model=AnalysisOut, status_code=201, tags=["analysis"])
def create_analysis(run_id: int, mode: str = Query(
        None, description="hybrid (default): local parser first, model reads only what it cannot; "
                          "llm: model reads every line; local: no model call at all.")):
    """Analyse the run's SCRUBBED text, verify every row, store the result.

    Default (hybrid): a local parser reads the clear transaction lines; the model reads only the
    rest and writes insights.  If Groq is unavailable the analysis still completes locally and
    `validation.llm_problem` says why.

    Safety: the text is re-checked by the verifier first (HTTP 422, nothing sent,
    if it fails); every extracted transaction is checked against the statement;
    all totals are computed in Python.
    """
    run = _get_or_404(run_id)
    files = [(f["filename"], f["scrubbed_text"]) for f in run["files"]]
    try:
        result, validation = analysis.analyze_files(files, mode=mode)
    except analysis.AnalysisBlocked as e:
        raise HTTPException(422, detail={
            "message": "Blocked: the stored text still looks like it contains identifiers, "
                       "so NOTHING was sent to Groq.", "findings": e.findings})
    except ValueError as e:                              # unknown mode
        raise HTTPException(422, str(e))
    except analysis.DailyLimitReached as e:
        raise HTTPException(429, str(e), headers={"X-Retry-In": e.retry_in or ""})
    except analysis.AnalysisError as e:
        code = 503 if str(e).startswith("GROQ_API_KEY") else 502
        raise HTTPException(code, str(e))
    try:
        aid = db.save_analysis(run_id, validation["model"], analysis.PROMPT_VERSION, result, validation)
        return _analysis_out(db.get_latest_analysis(run_id) | {"id": aid})
    except SQLAlchemyError:
        raise HTTPException(503, "Database unavailable. Is PostgreSQL running?")


@app.get("/runs/{run_id}/analysis", response_model=AnalysisOut, tags=["analysis"])
def get_analysis(run_id: int):
    _get_or_404(run_id)
    row = db.get_latest_analysis(run_id)
    if row is None:
        raise HTTPException(404, f"Run {run_id} has no analysis yet. POST to this URL to create one.")
    return _analysis_out(row)


@app.delete("/runs/{run_id}", status_code=204, tags=["runs"])
def delete_run(run_id: int):
    _get_or_404(run_id)
    db.delete_run(run_id)


# The frontend (Step 9) is served by the same app, so there is no CORS to configure.
# Mounted last so it never shadows an API route.
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
