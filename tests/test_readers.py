"""Reader tests.  pytest -v"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.readers import read_file          # noqa: E402
from app.engine import scrub_folder        # noqa: E402


def test_csv_keeps_leading_zeros_and_amounts(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text('Date,Amount,Phone\n2026-04-01,"+25,000.00",03001234567\n', encoding="utf-8")
    out = read_file(f)
    assert "03001234567" in out          # dtype=str: leading zero not lost
    assert '"+25,000.00"' in out         # amount untouched, quoting kept
    assert "Date,Amount,Phone" in out    # header row not swallowed


def test_csv_with_uneven_rows_loses_nothing(tmp_path):
    f = tmp_path / "ragged.csv"
    f.write_text("Account Holder,Bilal\nWallet ID,abc\nDate,Desc,Amount\n2026-04-01,Netflix.com,-3.99\n",
                 encoding="utf-8")
    out = read_file(f)
    assert "Netflix.com" in out and "Wallet ID,abc" in out


def test_txt_cp1252_encoding(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes("Paid £25.00 to Tesco".encode("cp1252"))
    assert "£25.00" in read_file(f)


def test_unsupported_type_is_reported_not_crashing(tmp_path):
    (tmp_path / "pic.png").write_bytes(b"\x89PNG")
    (tmp_path / "ok.txt").write_text("hello", encoding="utf-8")
    out = scrub_folder(tmp_path)
    assert "---- File: ok.txt ----" in out and "[SKIPPED: ValueError: unsupported file type: .png]" in out


def test_missing_folder_gives_clear_error(tmp_path):
    import pytest
    with pytest.raises(NotADirectoryError, match="Data folder not found"):
        scrub_folder(tmp_path / "nope")
