-- PII Scrubber - PostgreSQL schema.  Safe to run repeatedly (IF NOT EXISTS).
--
-- GOLDEN RULE: this database only ever holds SCRUBBED text.  Raw statement
-- text is never written here (it lives only in memory inside readers.py ->
-- scrubber.py).
--
-- Relationships:
--   scrub_runs 1 --- * scrubbed_files     one row per input file
--   scrub_runs 1 --- * analyses           Groq analysis results (Step 7)
--   scrub_runs 1 --- * chat_messages      chat history          (Step 8)

-- One row per "scrub the whole folder" action.  This is the unit everything
-- else hangs off: the analysis and the chat both refer to a run.
CREATE TABLE IF NOT EXISTS scrub_runs (
    id                  BIGSERIAL    PRIMARY KEY,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    file_count          INTEGER      NOT NULL CHECK (file_count >= 0),
    -- The exact single string the engine produced (per-file headers included).
    -- It is what gets sent to Groq, so we keep it byte-for-byte for
    -- reproducibility and to ground/verify the model's numbers later.
    combined_text       TEXT         NOT NULL,
    placeholder_counts  JSONB        NOT NULL DEFAULT '{}'::jsonb
);

-- One row per input file inside a run: lets you inspect or query each
-- statement separately and see which files were skipped and why.
CREATE TABLE IF NOT EXISTS scrubbed_files (
    id                  BIGSERIAL    PRIMARY KEY,
    run_id              BIGINT       NOT NULL REFERENCES scrub_runs(id) ON DELETE CASCADE,
    filename            TEXT         NOT NULL,
    file_type           TEXT         NOT NULL,                 -- '.pdf', '.csv', ...
    status              TEXT         NOT NULL CHECK (status IN ('ok', 'skipped')),
    scrubbed_text       TEXT         NOT NULL,                 -- or the "[SKIPPED: reason]" note
    placeholder_counts  JSONB        NOT NULL DEFAULT '{}'::jsonb,  -- {"<IBAN>": 3, ...}
    UNIQUE (run_id, filename)
);

-- Groq analysis of a run (filled in Step 7).  JSONB because the analysis is
-- structured (categories, merchants, insights) and may evolve between prompt
-- versions.  Several analyses per run are allowed (re-run with another model).
CREATE TABLE IF NOT EXISTS analyses (
    id                  BIGSERIAL    PRIMARY KEY,
    run_id              BIGINT       NOT NULL REFERENCES scrub_runs(id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    model               TEXT         NOT NULL,
    prompt_version      TEXT         NOT NULL,
    result              JSONB        NOT NULL,   -- the model's structured analysis
    validation          JSONB        NOT NULL DEFAULT '{}'::jsonb  -- our grounding/number checks
);

-- Chat turns about a run (Step 8).  Stored so the conversation survives a page
-- refresh and the API can rebuild the history window for each new question.
CREATE TABLE IF NOT EXISTS chat_messages (
    id                  BIGSERIAL    PRIMARY KEY,
    run_id              BIGINT       NOT NULL REFERENCES scrub_runs(id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    role                TEXT         NOT NULL CHECK (role IN ('user', 'assistant')),
    content             TEXT         NOT NULL
);

-- Foreign keys are not indexed automatically in PostgreSQL.  These make
-- "all files / analyses / chat of run X, in order" fast.
CREATE INDEX IF NOT EXISTS idx_files_run     ON scrubbed_files (run_id);
CREATE INDEX IF NOT EXISTS idx_analyses_run  ON analyses (run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_run      ON chat_messages (run_id, id);
