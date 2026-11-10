"""Step 3 manual review helper.   python tests/review.py [folder]
1) placeholder counts per file  2) suspicious leftovers to eyeball  3) side-by-side of changed lines
"""
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.readers import read_file
from app.scrubber import scrub_text

folder = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
SUSPECT = re.compile(r"(?<![\d.,])\d{6,}(?![\d]|[.,]\d)|\b(?!PKR|USD|xxxx|XXXX|Apr)[A-Za-z]+-?[A-Za-z0-9]*\d{3,}[A-Za-z0-9-]*\b")
IGNORE = re.compile(r"^(PKR|USD|x-?\d|xxxx|XXXX|Apr-)", re.I)

for path in sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")):
    raw = read_file(path)
    out = scrub_text(raw)
    print(f"\n{'=' * 70}\n{path.name}\n{'=' * 70}")
    print("Placeholders:", dict(Counter(re.findall(r"<[A-Z_]+>", out))))
    left = Counter(m.group() for m in SUSPECT.finditer(out) if not IGNORE.match(m.group()))
    print("Still present, eyeball these (identifier or fine?):")
    for tok, n in left.most_common():
        print(f"   {tok}  x{n}")

input("Press Enter to end it...")