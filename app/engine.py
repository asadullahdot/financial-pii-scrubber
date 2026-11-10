"""Folder in -> ONE combined scrubbed string out. No hardcoded filenames."""
from pathlib import Path
from readers import read_file
from scrubber import scrub_text


def scrub_folder(folder: str | Path) -> str:
    folder = Path(folder)
    parts = []
    for path in sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")):
        header = f"---- File: {path.name} ----"
        try:
            body = scrub_text(read_file(path))
        except Exception as e:  # one bad file must not kill the run
            body = f"[SKIPPED: {type(e).__name__}: {e}]"
        parts.append(f"{header}\n{body.strip()}\n")
    return "\n".join(parts)
