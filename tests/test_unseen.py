"""Generalisation test: statements from countries that are NOT in data/ (UAE, Saudi Arabia, Australia,
South Africa, Singapore, Nigeria, Kenya, plus an ID-format mix and a file of precision lookalikes).

tests/fixtures/unseen_countries/ holds invented statements (TXT, CSV, DOCX) and _truth.json:
  must_remove  every identifier planted in them (it must NOT appear in the output)
  must_keep    names, merchants, amounts and dates that must survive untouched
All values are made up; none belongs to a real person.  Every defect this folder exposed was
turned into a rule and stays covered here.  See also the focused unit tests below.
"""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.engine import scrub_folder          # noqa: E402
from app.scrubber import scrub_text          # noqa: E402
from app.verifier import verify_clean        # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "unseen_countries"
TRUTH = json.loads((FIXTURES / "_truth.json").read_text(encoding="utf-8"))


def _squash(s: str) -> str:
    return " ".join(s.split())


@pytest.fixture(scope="module")
def output(tmp_path_factory):
    work = tmp_path_factory.mktemp("unseen")
    for p in FIXTURES.iterdir():
        if p.name != "_truth.json":
            (work / p.name).write_bytes(p.read_bytes())
    return _squash(scrub_folder(str(work)))


@pytest.mark.parametrize("secret", TRUTH["must_remove"])
def test_planted_identifier_is_removed(output, secret):
    assert _squash(secret) not in output


@pytest.mark.parametrize("keep", sorted(set(TRUTH["must_keep"])))
def test_meaning_survives(output, keep):
    assert _squash(keep) in output


def test_every_file_has_a_header_and_the_independent_verifier_agrees(output):
    assert output.count("---- File:") == len([p for p in FIXTURES.iterdir() if p.name != "_truth.json"])
    assert verify_clean(output).ok


# ---------------------------------------------------------------- focused regressions (one per defect found)
@pytest.mark.parametrize("raw, expected", [
    # CSV "label,value" lines and a phone right after a comma
    ("Mobile,0501234567", "Mobile,<PHONE>"),
    ("Alt Mobile,+966 50 765 4321", "Alt Mobile,<PHONE>"),
    ("Iqama No,2345678901", "Iqama No,<NATIONAL_ID>"),
    ("Home: 6234 5678", "Home: <PHONE>"),
    ("Landline: 04 345 6789", "Landline: <PHONE>"),
    ("Cust,0722 987 654,x", "Cust,<PHONE>,x"),
    # labels that were missing
    ("Tax File Number: 123 456 782", "Tax File Number: <NATIONAL_ID>"),
    ("Branch code: 250655", "Branch code: <ROUTING_ID>"),
    ("NUBAN: 0123456789", "NUBAN: <ACCOUNT_ID>"),
    ("Passport No: X1234567", "Passport No: <NATIONAL_ID>"),
    # fixed-shape IDs
    ("NRIC/FIN: S1234567D", "NRIC/FIN: <NATIONAL_ID>"),
    ("CPF: 123.456.789-09", "CPF: <NATIONAL_ID>"),
    ("Personnummer: 19850101-1234", "Personnummer: <NATIONAL_ID>"),
    # a card that shows its first six digits
    ("Card 4847 12** **** 3456 POS", "Card <CARD_NUMBER> POS"),
    # a long number followed by another CSV column
    ("SEC BILLER 1234567890,380.00,,13614.95", "SEC BILLER <REF_ID>,380.00,,13614.95"),
])
def test_defects_found_on_unseen_countries(raw, expected):
    assert scrub_text(raw) == expected


@pytest.mark.parametrize("line", [
    # amounts next to an account number must not be eaten (the "ACC 5566778899   486.20" bug)
    "05/04/2026  DEWA BILL PAYMENT ACC <ACCOUNT_ID>   486.20   0.00   17,431.45",
    # things that look like identifiers but are not
    "12 Apr 2026 7-ELEVEN 1234 SYDNEY NSW AU -$8.50 $1,000.00",
    "15 Apr 2026 LOTTERY PAYOUT +$1,234,567.89 $1,235,500.41",
    "16 Apr 2026 JPY 1,234,567 TRANSFER TO PARTNER -JPY 1,234,567 JPY 9,999,999",
    "2026-04-02,MADA POS PANDA RETAIL RIYADH Card xxxx3456,215.40,,13994.95,SAR",
    "9500.00,14210.35,SAR",
    "02 Apr 2026 SALARY 1,234.56 2,000.00",
    "1 000,00 R 29 174,55",
])
def test_lookalikes_are_not_changed(line):
    assert scrub_text(line) == line


def test_account_value_stops_at_wide_column_padding():
    out = scrub_text("DEWA BILL PAYMENT ACC 5566778899                                    486.20       0.00")
    assert "486.20" in out and "5566778899" not in out
