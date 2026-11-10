"""Step 1: file readers. Every reader takes a Path and returns raw text.

New formats = write one function and register it in READERS.
Nothing here ever touches the network.
"""
from pathlib import Path


def read_txt(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):  # latin-1 never fails
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_pdf(path: Path) -> str:
    import pdfplumber

    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    text = "\n".join(pages).strip()
    if not text:
        raise ValueError("no extractable text (scanned PDF? OCR not supported)")
    return text


def read_docx(path: Path) -> str:
    """Paragraphs AND tables in document order, plus headers/footers."""
    import docx
    from docx.oxml.ns import qn

    d = docx.Document(path)
    lines = []

    def para_text(el):
        return "".join(t.text or "" for t in el.iter(qn("w:t")))

    for section in d.sections:  # headers/footers often hold account numbers
        for part in (section.header, section.footer):
            for p in part.paragraphs:
                if p.text.strip():
                    lines.append(p.text)

    for child in d.element.body.iterchildren():
        if child.tag == qn("w:p"):
            t = para_text(child)
            if t.strip():
                lines.append(t)
        elif child.tag == qn("w:tbl"):
            for tr in child.iter(qn("w:tr")):
                cells = [para_text(tc).strip() for tc in tr.iter(qn("w:tc"))]
                lines.append(" | ".join(cells))
    return "\n".join(lines)


# def read_xlsx(path: Path) -> str:
#     import openpyxl

#     wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
#     lines = []
#     for ws in wb.worksheets:
#         lines.append(f"[Sheet: {ws.title}]")
#         for row in ws.iter_rows(values_only=True):
#             if any(c is not None for c in row):
#                 lines.append(", ".join("" if c is None else str(c) for c in row))
#     return "\n".join(lines)


READERS = {
    ".txt": read_txt, ".csv": read_txt, ".tsv": read_txt, ".md": read_txt,
    ".pdf": read_pdf,
    ".docx": read_docx,
    # ".xlsx": read_xlsx, ".xlsm": read_xlsx,
}


def read_file(path: Path) -> str:
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        raise ValueError(f"unsupported file type: {path.suffix or '(none)'}")
    return reader(path)
