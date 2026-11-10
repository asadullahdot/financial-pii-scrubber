"""Step 4: the engine. FOLDER IN -> ONE COMBINED SCRUBBED STRING OUT.

    scrub_folder("data")  ->

        ---- File: crescent_bank_statement_apr2026.pdf ----
        <scrubbed text of the PDF>

        ---- File: paypal_activity_apr2026.txt ----
        <scrubbed text of the TXT>
        ...

This module is the only thing the rest of the project needs to call:
    readers.py   turns each file into raw text          (contains PII)
    scrubber.py  replaces identifiers with placeholders (removes PII)
    engine.py    runs both on every file and joins the results

No file names are hard-coded.  Every file in the folder is processed, in
alphabetical order so the output is the same every run.
"""
import sys
from pathlib import Path

from app.readers import read_file
from app.scrubber import scrub_text

# The project root is the folder that contains app/.  Default paths are built
# from it so the script works no matter which folder your terminal is in.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_OUTPUT_FILE = PROJECT_ROOT / "output" / "combined_scrubbed.txt"


def scrub_folder(folder: str | Path) -> str:
    """Scrub every file in `folder` and return ONE combined string.

    A file that cannot be read (unsupported type, corrupt, scanned PDF) does
    NOT stop the run: it appears in the output as a "[SKIPPED: reason]" line
    so one bad file never loses the rest of the statements.
    """
    folder = Path(folder)

    # Fail early with a message that shows exactly where we looked.
    if not folder.is_dir():
        raise NotADirectoryError(
            f"Data folder not found: {folder.resolve()}\n"
            f"Create it and put your statements inside, or pass another folder."
        )

    parts = []  # one block of text per file

    # sorted() -> deterministic order.   is_file() -> ignore sub-folders.
    # not startswith(".") -> ignore hidden files such as .DS_Store or .gitkeep.
    files = sorted(p for p in folder.iterdir()
                   if p.is_file() and not p.name.startswith("."))

    for path in files:
        header = f"---- File: {path.name} ----"
        try:
            raw_text = read_file(path)      # readers.py: file -> raw text (has PII)
            body = scrub_text(raw_text)     # scrubber.py: raw text -> safe text
        except Exception as e:
            # Report only the error type/message, never the file's contents.
            body = f"[SKIPPED: {type(e).__name__}: {e}]"
        parts.append(f"{header}\n{body.strip()}\n")

    # A blank line between files keeps the combined string easy to read.
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Command-line use (run from anywhere):
#     python -m app.engine                       data/  ->  output/combined_scrubbed.txt
#     python -m app.engine my_folder             a different input folder
#     python -m app.engine my_folder result.txt  a different output file too
# --------------------------------------------------------------------------
if __name__ == "__main__":
    # Windows terminals often use a legacy encoding (cp1252) that cannot print
    # symbols like the rupee sign or other currency signs.  Force UTF-8 so
    # print() never crashes.  (Files are always written as UTF-8 below.)
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
