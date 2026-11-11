"""API response models.  They document the API in /docs and guarantee that
only the fields listed here ever leave the server."""
from datetime import datetime

from pydantic import BaseModel, Field


class FileOut(BaseModel):
    filename: str
    file_type: str
    status: str = Field(description="'ok' or 'skipped'")
    chars: int = Field(description="length of the scrubbed text")
    placeholder_counts: dict[str, int] = Field(description='e.g. {"<IBAN>": 3}')


class RunSummary(BaseModel):
    run_id: int
    created_at: datetime
    file_count: int
    placeholder_counts: dict[str, int]


class RunDetail(RunSummary):
    files: list[FileOut]
    combined_text: str = Field(description="ONE scrubbed string, with a '---- File: name ----' header per file")


class FolderRequest(BaseModel):
    folder: str = Field(default="data", description="server-side folder; relative paths are inside the project")


class Health(BaseModel):
    status: str
    database: str


class AnalysisOut(BaseModel):
    analysis_id: int
    run_id: int
    created_at: datetime
    model: str
    prompt_version: str
    result: dict = Field(description="verified aggregates, insights and the verified transactions")
    validation: dict = Field(description="what the guardrails checked and dropped")


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000,
                          description="Your question. Identifiers in it are scrubbed before anything is sent.")


class ChatSource(BaseModel):
    id: int
    date: str
    currency: str
    amount: float
    direction: str
    category: str
    merchant: str | None = None


class ChatOut(BaseModel):
    answer: str
    verified: bool = Field(description="every number in the answer was checked against the data")
    in_scope: bool
    question_as_sent: str = Field(description="your question after scrubbing")
    computed: dict = Field(description="the app's own totals over the cited rows, per currency")
    sources: list[ChatSource] = Field(description="the transactions the answer is based on")
    history_turns_used: int
    attempts: int
    unverified_numbers_caught: int


class ChatMessageOut(BaseModel):
    id: int
    created_at: datetime
    role: str
    content: str
