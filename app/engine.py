"""Step 4: the engine. FOLDER IN -> ONE COMBINED SCRUBBED STRING OUT.

    scrub_folder("data")  ->

        ---- File: crescent_bank_statement_apr2026.pdf ----
        <scrubbed text of the PDF>

        ---- File: paypal_activity_apr2026.txt ----
        <scrubbed text of the TXT>
        ...

Two levels of API:
    scrub_files(folder)  -> list[ScrubbedFile]   one result per file (used by the
                                                 database layer to store one row each)
    combine(files)       -> str                  the single combined string
    scrub_folder(folder) -> str                  both steps in one call

    readers.py   turns each file into raw text          (contains PII)
    scrubber.py  replaces identifiers with placeholders (removes PII)
    engine.py    runs both on every file and joins the results

No file names are hard-coded.  Every file in the folder is processed, in
alphabetical order so the output is the same every run.
"""
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.readers import read_file
from app.scrubber import scrub_text

# The project root is the folder that contains app/.  Default paths are built
# from it so the script works no matter which folder your terminal is in.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_OUTPUT_FILE = PROJECT_ROOT / "output" / "combined_scrubbed.txt"


@dataclass
class ScrubbedFile:
    """The scrubbed result for ONE input file (never contains raw text)."""
    name: str                      # original file name, e.g. "paypal_activity_apr2026.txt"
    file_type: str                 # lower-case extension, e.g. ".txt"
    status: str                    # "ok" or "skipped"
    text: str                      # scrubbed text, or "[SKIPPED: reason]"
    placeholder_counts: dict = field(default_factory=dict)  # {"<IBAN>": 3, ...}


def scrub_files(folder: str | Path) -> list[ScrubbedFile]:
    """Scrub every file in `folder`; return one ScrubbedFile per file.

    A file that cannot be read (unsupported type, corrupt, scanned PDF) does
    NOT stop the run: it comes back with status "skipped" so one bad file
    never loses the rest of the statements.
    """
    folder = Path(folder)

    # Fail early with a message that shows exactly where we looked.
    if not folder.is_dir():
        raise NotADirectoryError(
            f"Data folder not found: {folder.resolve()}\n"
            f"Create it and put your statements inside, or pass another folder."
        )

    # sorted() -> deterministic order.   is_file() -> ignore sub-folders.
    # not startswith(".") -> ignore hidden files such as .DS_Store or .gitkeep.
    paths = sorted(p for p in folder.iterdir()
                   if p.is_file() and not p.name.startswith("."))

    results = []
    for path in paths:
        try:
            raw_text = read_file(path)      # readers.py: file -> raw text (has PII)
            text = scrub_text(raw_text)     # scrubber.py: raw text -> safe text
            status = "ok"
        except Exception as e:
            # Report only the error type/message, never the file's contents.
            text, status = f"[SKIPPED: {type(e).__name__}: {e}]", "skipped"
        counts = dict(Counter(re.findall(r"<[A-Z_]+>", text)))
        results.append(ScrubbedFile(path.name, path.suffix.lower(), status, text.strip(), counts))
    return results


def combine(files: list[ScrubbedFile]) -> str:
    """Join per-file results into ONE string with a header before each file."""
    # A blank line between files keeps the combined string easy to read.
    return "\n".join(f"---- File: {f.name} ----\n{f.text}\n" for f in files)


def scrub_folder(folder: str | Path) -> str:
    """Scrub every file in `folder` and return ONE combined string."""
    return combine(scrub_files(folder))


# --------------------------------------------------------------------------
# Command-line use (run from the project root):
#     python -m app.engine                       data/  ->  output/combined_scrubbed.txt
#     python -m app.engine my_folder             a different input folder
#     python -m app.engine my_folder result.txt  a different output file too
# --------------------------------------------------------------------------
if __name__ == "__main__":
    # Windows terminals often use a legacy encoding (cp1252) that cannot print
    # symbols like the rupee sign.  Force UTF-8 so print() never crashes.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATA_DIR
    out_file = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT_FILE

    try:
        result = scrub_folder(folder)
    except NotADirectoryError as e:
        sys.exit(f"ERROR: {e}")  # clean message instead of a long traceback

    out_file.parent.mkdir(parents=True, exist_ok=True)   # create output/ if missing
    out_file.write_text(result, encoding="utf-8")
    print(result)
    print(f"\n[saved to {out_file.resolve()}]")
