"""Step 1: file readers.

Every reader takes a file Path and returns the file's contents as ONE plain
text string.  The text is RAW (it still contains PII), so it must only ever be
passed straight into the scrubber and never saved, logged or sent anywhere.

Which reader is used is decided by the file extension (see READERS at the
bottom).  To support a new format: write one read_xxx() function and add it
to READERS.  Nothing in this file touches the network.
"""
import csv
from pathlib import Path

import pandas as pd

# Encodings to try, in order.  Bank exports are usually UTF-8, but older
# Windows exports are often cp1252.  latin-1 can decode ANY byte sequence, so
# it is the last resort and guarantees we never crash on encoding.
ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


# --------------------------------------------------------------------------
# TXT  ->  plain open()
# --------------------------------------------------------------------------
def read_txt(path: Path) -> str:
    """Read a plain text file with open(), trying several encodings."""
    for enc in ENCODINGS:
        try:
            # newline=None lets Python normalise \r\n (Windows) to \n
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue  # wrong guess, try the next encoding
    # Unreachable in practice (latin-1 never fails) but keeps the function total.
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# --------------------------------------------------------------------------
# CSV / TSV  ->  pandas DataFrame
# --------------------------------------------------------------------------
def _dataframe_to_text(df: pd.DataFrame) -> str:
    """Turn a DataFrame back into CSV-style text, one line per row."""
    # header=False because we read with header=None: the first row of the file
    # is just another row, so a real header line is output like any other line.
    text = df.to_csv(index=False, header=False, lineterminator="\n")
    # Short rows get padded with empty cells ("a,b,,,"); drop those trailing commas.
    return "\n".join(line.rstrip(",") for line in text.splitlines())


def read_csv(path: Path) -> str:
    """Read a CSV/TSV with pandas and return it as text.

    Settings that matter for a *scrubber*:
      dtype=str            keep every cell exactly as written. Without it pandas
                           turns "0300123" into the number 300123 (leading zero
                           lost) and "61050.00" into a float, which would break
                           the regex rules.
      keep_default_na=False  don't turn "NA"/"null"/"" into NaN (that would
                           silently delete cells).
      header=None          never treat the first row as a header; every line of
                           the file is kept as data.
      sep=None + python    let pandas sniff the delimiter (comma, semicolon, tab).
    """
    last_error = None
    for enc in ENCODINGS:
        try:
            try:
                df = pd.read_csv(path, header=None, dtype=str, keep_default_na=False,
                                 sep=None, engine="python", encoding=enc,
                                 skip_blank_lines=True)
            except pd.errors.ParserError:
                # Rows with different numbers of columns (e.g. a few metadata
                # lines above the table) make pandas fail.  Find the widest row
                # and force that many columns so NO row is dropped.
                with open(path, newline="", encoding=enc) as f:
                    width = max((len(r) for r in csv.reader(f)), default=1)
                df = pd.read_csv(path, header=None, dtype=str, keep_default_na=False,
                                 names=range(width), engine="python", encoding=enc,
                                 skip_blank_lines=True)
            return _dataframe_to_text(df)
        except UnicodeDecodeError as e:
            last_error = e  # try the next encoding
    raise ValueError(f"could not decode CSV: {last_error}")


# --------------------------------------------------------------------------
# PDF  ->  pdfplumber
# --------------------------------------------------------------------------
def read_pdf(path: Path) -> str:
    """Extract the text layer of every page.  Scanned PDFs (images) have no
    text layer; we raise a clear error instead of returning an empty string."""
    import pdfplumber

    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    text = "\n".join(pages).strip()
    if not text:
        raise ValueError("no extractable text (scanned PDF? OCR not supported)")
    return text


# --------------------------------------------------------------------------
# DOCX  ->  python-docx
# --------------------------------------------------------------------------
def read_docx(path: Path) -> str:
    """Return paragraphs AND tables in document order, plus headers/footers.

    python-docx's d.paragraphs ignores tables, and bank statements are mostly
    tables, so we walk the document body element by element instead.
    """
    import docx
    from docx.oxml.ns import qn

    d = docx.Document(path)
    lines = []

    def text_of(element):
        # A Word element stores its text in <w:t> tags; join them all.
        return "".join(t.text or "" for t in element.iter(qn("w:t")))

    # Headers/footers often carry account numbers, so include them.
    for section in d.sections:
        for part in (section.header, section.footer):
            for p in part.paragraphs:
                if p.text.strip():
                    lines.append(p.text)

    for child in d.element.body.iterchildren():
        if child.tag == qn("w:p"):                       # normal paragraph
            t = text_of(child)
            if t.strip():
                lines.append(t)
        elif child.tag == qn("w:tbl"):                   # table: one line per row
            for tr in child.iter(qn("w:tr")):
                cells = [text_of(tc).strip() for tc in tr.iter(qn("w:tc"))]
                lines.append(" | ".join(cells))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# XLSX  ->  pandas DataFrame (one block per sheet)
# --------------------------------------------------------------------------
def read_excel(path: Path) -> str:
    """Read every sheet of an Excel workbook with pandas."""
    # sheet_name=None -> dict of {sheet name: DataFrame}.  header=None and
    # dtype=str for the same reasons as read_csv.
    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=str)
    blocks = []
    for name, df in sheets.items():
        blocks.append(f"[Sheet: {name}]\n{_dataframe_to_text(df.fillna(''))}")
    return "\n".join(blocks)


# --------------------------------------------------------------------------
# Dispatch table: file extension -> reader function
# --------------------------------------------------------------------------
READERS = {
    ".txt": read_txt, ".md": read_txt, ".log": read_txt,
    ".csv": read_csv, ".tsv": read_csv,
    ".pdf": read_pdf,
    ".docx": read_docx,
    ".xlsx": read_excel, ".xlsm": read_excel,
}


def read_file(path: Path) -> str:
    """Pick the right reader from the extension (case-insensitive)."""
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        raise ValueError(f"unsupported file type: {path.suffix or '(none)'}")
    return reader(path)
