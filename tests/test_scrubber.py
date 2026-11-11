"""Regression suite.  Run from project root:  pytest -v

Layer 1  unit cases       - tiny synthetic inputs, independent of your data files
Layer 2  generic leak scan - pattern-based, works on ANY folder (no known strings)
Layer 3  data/ regression  - exact strings from the provided files
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.engine import scrub_folder          # noqa: E402
from app.scrubber import scrub_text          # noqa: E402

# ---------------------------------------------------------------- Layer 1
# (input, substring that must be GONE, placeholder that must appear)
REMOVE_CASES = [
    ("IBAN AE070331234567890123456", "AE0703312345", "<IBAN>"),
    ("IBAN: FR14 2004 1010 0505 0001 3M02 606", "2004 1010", "<IBAN>"),
    ("iban nl91abna0417164300", "nl91abna", "<IBAN>"),
    ("Account Number: 123456789", "123456789", "<ACCOUNT_ID>"),
    ("A/C No: 0011223344556677", "0011223344556677", "<ACCOUNT_ID>"),
    ("BSB: 062-000  Account: 12345678", "12345678", "<ACCOUNT_ID>"),
    ("Routing Number: 021000021", "021000021", "<ROUTING_ID>"),
    ("Sort Code: 12-34-56", "12-34-56", "<ROUTING_ID>"),
    ("IFSC: HDFC0001234", "HDFC0001234", "<ROUTING_ID>"),
    ("BIC: DEUTDEFF", "DEUTDEFF", "<SWIFT_BIC>"),
    ("SWIFT: CHASUS33XXX", "CHASUS33", "<SWIFT_BIC>"),
    ("SSN: 123-45-6789", "123-45-6789", "<NATIONAL_ID>"),
    ("NI Number: JG103759A", "JG103759A", "<NATIONAL_ID>"),
    ("CNIC 35202-1234567-1", "35202-1234567-1", "<NATIONAL_ID>"),
    ("Emirates ID 784-1990-1234567-1", "1234567", "<NATIONAL_ID>"),
    ("Card 4111111111111111 used", "4111111111111111", "<CARD_NUMBER>"),
    ("Card 4111 1111 1111 1111 used", "4111 1111", "<CARD_NUMBER>"),
    ("Card 411111******1111", "411111", "<CARD_NUMBER>"),
    ("mail me at jane.doe+bank@example.co.uk now", "jane.doe", "<EMAIL>"),
    ("paid to someone@easywallet today", "someone@easywallet", "<PAYMENT_HANDLE>"),
    ("UPI amit@oksbi", "amit@oksbi", "<PAYMENT_HANDLE>"),
    ("Phone: +971 50 123 4567", "123 4567", "<PHONE>"),
    ("Phone: 0412 345 678", "345 678", "<PHONE>"),
    ("Tel (02) 9374 4000", "9374", "<PHONE>"),
    ("Call (212) 555-0199 now", "555-0199", "<PHONE>"),
    ("Mobile 03211234567", "03211234567", "<PHONE>"),
    ("Address: 12 Palm Street, Dubai, UAE", "Palm Street", "<ADDRESS>"),
    ("Billing Address: 5 Rue de Rivoli, Paris", "Rivoli", "<ADDRESS>"),
    ("Trace 123456789012", "123456789012", "<REF_ID>"),
    ("Ref: AB-99231144", "99231144", "<REF_ID>"),
    ("Txn 8XJ22190T7002841M done", "8XJ22190T7002841M", "<REF_ID>"),
    ("Login IP 192.168.10.45", "192.168.10.45", "<IP_ADDRESS>"),
]

# (input, substrings that must SURVIVE untouched)
KEEP_CASES = [
    ("Paid to Netflix.com Amsterdam", ["Netflix.com", "Amsterdam"]),
    ("TESCO STORES 3421 MANCHESTER GBR -£54.20 £3,066.24", ["TESCO STORES 3421", "-£54.20", "£3,066.24"]),
    ("04/01/2026 | STARBUCKS #08841 AUSTIN TX | -$7.25", ["04/01/2026", "#08841", "-$7.25"]),
    ("Card xxxx8821 PKR7,340.50Dr", ["xxxx8821", "PKR7,340.50Dr"]),
    ("Linked Bank: Crescent Bank ****6677", ["****6677"]),
    ("Money sent to Hamza Qureshi", ["Hamza Qureshi"]),
    ("2026-04-02 10:14:03 Raast In +25,000.00", ["2026-04-02 10:14:03", "+25,000.00"]),
    ("+PKR 61050.00 and +118300.00 received", ["+PKR 61050.00", "+118300.00"]),
    ("Balance ₹1,12,340.00 and €4.150,20", ["₹1,12,340.00", "€4.150,20"]),
    ("RESOLVED TRANSFER PAYMENTS MARKETPLACE", ["RESOLVED", "TRANSFER", "PAYMENTS", "MARKETPLACE"]),
    ("AMAZON.CO.UK MKTPLACE and Daraz.pk", ["AMAZON.CO.UK", "Daraz.pk"]),
    ("USD 3.99 @ 278.45 INCL FED", ["USD 3.99 @ 278.45"]),
    ("Branch: Gulshan-e-Iqbal Branch (0114), Karachi", ["Gulshan-e-Iqbal Branch", "Karachi"]),
    ("Account Holder: Priya Raghunathan", ["Priya Raghunathan"]),
]


# ---- Added after benchmarking on statement styles the rules were NOT tuned on ----
# Unlabelled address blocks, cashtags, FR/DE phones, AU/UAE/SA formats.
REMOVE_CASES += [
    ("Mr Ali Khan\n45 Palm Jumeirah Road\nDubai, UAE", "Palm Jumeirah", "<ADDRESS>"),
    ("Account holder: Sarah Mitchell\n12 Baker Street\nLondon NW1 6XE", "12 Baker", "<ADDRESS>"),
    ("Account holder: Sarah Mitchell\n12 Baker Street\nLondon NW1 6XE", "NW1 6XE", "<ADDRESS>"),
    ("12 BAKER STREET", "BAKER", "<ADDRESS>"),
    ("350 5th Avenue, New York, NY 10118", "5th Avenue", "<ADDRESS>"),
    ("7 Elm Ave | Austin TX 78701", "78701", "<ADDRESS>"),
    ("Home: 221B Rue Lafayette, 75009 Paris, France", "Lafayette", "<ADDRESS>"),
    ("Home: 221B Rue Lafayette, 75009 Paris, France", "75009", "<ADDRESS>"),
    ("Bergmannstraße 44, 10961 Berlin", "Bergmann", "<ADDRESS>"),
    ("House B-47, Street 11, Gulshan Block 6, Karachi", "B-47", "<ADDRESS>"),
    ("Toronto M5V 2T6", "M5V 2T6", "<ADDRESS>"),
    ("Cash App $johndoe99 sent $25.00", "johndoe99", "<PAYMENT_HANDLE>"),
    ("Tel 01 42 68 53 00", "42 68 53", "<PHONE>"),
    ("Tel 030 12345678", "12345678", "<PHONE>"),
    ("BSB 062-000 Account 1234 5678 PayID sam@payid.com.au", "1234 5678", "<ACCOUNT_ID>"),
    ("IBAN SA03 8000 0000 6080 1016 7519", "8000 0000", "<IBAN>"),
    ("Remitter: ACME LTD, SWIFT/BIC: CITIUS33", "CITIUS33", "<SWIFT_BIC>"),
]

# Lines that LOOK like addresses but are transactions: must come out unchanged.
KEEP_UNCHANGED = [
    "12 Apr 2026 ATM WDL CRESCENT ATM GULSHAN BR Card xxxx8821 Trace",
    "2026-04-23 20:02:59,POS,PAID TO SHANDAR SHINWARI RESTAURANT NATIONAL HIGHWAY KARACHI PK|Visa xxxx2070",
    "CHEQUE NO 12345 CLEARING FAVOURING AL-REHMAN",
    "Transfer 25000 Hamza Qureshi on 2026-04-02",
    "Paid via PayPal to Netflix.com",
    "TIM HORTONS #4471 TORONTO ON",
    "STARBUCKS #08841 AUSTIN TX",
    "Paid 50 USD to Park Lane Hotel",
    "Spent 120 on Drive-in Cinema",
    "Order of 3 items from Oak Road Bakery",
    "01 Apr 2026 BAL B/F PKR18,450.75",
    "Rate USD 3.99 @ 278.45 INCL FED",
    "2 Apr 2026 Square Inc payment",
    "5 STAR HOTEL ROAD SIDE CAFE",
    "Paid C$4,880.19 and A$100.00 and $25.00",
]


@pytest.mark.parametrize("line", KEEP_UNCHANGED)
def test_address_rules_do_not_over_scrub(line):
    assert scrub_text(line) == line


@pytest.mark.parametrize("text,gone,placeholder", REMOVE_CASES)
def test_removes(text, gone, placeholder):
    out = scrub_text(text)
    assert gone not in out, f"LEAK: {out!r}"
    if placeholder:
        assert placeholder in out, f"wrong/no placeholder: {out!r}"


@pytest.mark.parametrize("text,keep", KEEP_CASES)
def test_keeps(text, keep):
    out = scrub_text(text)
    for k in keep:
        assert k in out, f"OVER-SCRUBBED {k!r}: {out!r}"


def test_wrapped_iban_is_rejoined():
    out = scrub_text("IBAN: PK36SCBL00000011234567\n02\nCNIC No 42201-7654321-3")
    assert "02\n" not in out and "<IBAN>\n<NATIONAL_ID>" in out.replace("CNIC No ", "")


# ---------------------------------------------------------------- Layer 2
# Generic: no known strings. Anything still matching is a probable leak.
from app.verifier import LEAK_PATTERNS, find_leaks  # noqa: E402  (single source of truth)


def leak_scan(text):
    return find_leaks(text)


@pytest.fixture(scope="session")
def combined():
    data = ROOT / "data"
    if not data.exists():
        pytest.skip("no data/ folder")
    out = scrub_folder(data)
    (ROOT / "output").mkdir(exist_ok=True)
    (ROOT / "output" / "combined_scrubbed.txt").write_text(out, encoding="utf-8")
    return out


def test_generic_leak_scan_on_data_folder(combined):
    assert leak_scan(combined) == {}


def test_every_file_has_a_header_and_none_skipped(combined):
    files = [p.name for p in (ROOT / "data").iterdir() if p.is_file() and not p.name.startswith(".")]
    for f in files:
        assert f"---- File: {f} ----" in combined
    assert "[SKIPPED" not in combined


# ---------------------------------------------------------------- Layer 3
REGRESSION_REMOVE = """bilal.hkhan91@gmail.com 0011223344556677 PK36SCBL0000001123456702 PK91NAYA0000003001234567
42201-7654321-3 0300-1234567 03001234567 +92-300-1234567 0333-1234567 9876543 hamzaq7788 ayesha.sid
hamza.qureshi.dev payments@upwork 8899-0011-2233-4455 521234 SCBLPKKA SADAPKKA NWBKGB2L COBADEFF
240930066601122334 662209118845 8XJ22190T7002841M PP-D-1122334455 888-221-1161 39.51.202.114 B-47
Church Lane GB29 DE89 3704 AB123456C 078-05-1120 046454286 345 678 903 4111-2222-3333 ABCPD1234F
SVBL0001234 SBIN0011223 021000021 30800 402219 99887766 55667788 000456789012 50100987654321 okaxis
okhdfc carlos-r-tx bilalhk91 900123 555-0148 555-0177 98765432 98220 43210 40012345678901
88123456700021 Willow Creek Lakeshore Bergmann MG Road 091000019992213 rohan.raghu
CRES2604020014561234567890123 N091260044556677 609912334455 44120933 NG-PAY-0401 7712003456""".split()

REGRESSION_KEEP = [
    "Bilal Hassan Khan", "Netflix.com", "NETFLIX.COM", "Daraz.pk", "AMAZON.CO.UK", "TESCO STORES 3421",
    "Foodpanda Karachi", "IMTIAZ SUPER MARKET", "Hamza Qureshi", "Ayesha Siddiqui", "Daniel R. Whitfield",
    "Marcus T. Alvarez", "Priya Raghunathan", "Lukas Brandt", "Rahul Deshpande", "xxxx8821", "xxxx2070",
    "****6677", "XXXXXX4321", "Card ending 4532", "PKR185,000.00Cr", "+£2,450.00", "₹1,12,340.00",
    "€4.150,20", "C$4,880.19", "+PKR 61050.00", '"+25,000.00"', "2026-04-01 08:55:12",
    "2026-Apr-02 10:14:22", "04/01/2026", "01.04.2026", "USD 3.99 @ 278.45", "STARBUCKS #08841",
    "GITHUB, INC.", "Spotify AB", "K-Electric", "Steam Games (Valve Corp)", "Upwork Escrow Inc",
    "TECHVERSE SOLUTIONS PVT LTD", "CHECK #1102",
]


@pytest.mark.parametrize("token", REGRESSION_REMOVE)
def test_regression_removed(combined, token):
    assert token not in combined


@pytest.mark.parametrize("token", REGRESSION_KEEP)
def test_regression_kept(combined, token):
    assert token in combined


def test_international_phone_does_not_swallow_the_next_line():
    """Found in the browser with a synthetic UK statement: the separator class included a newline,
    so '+44 7700 900123' + newline + '01 Apr 2026' lost the date's day and the line break."""
    from app.scrubber import scrub_text
    assert scrub_text("Phone: +44 7700 900123\n01 Apr 2026 | X") == "Phone: <PHONE>\n01 Apr 2026 | X"
    assert scrub_text("Cell # +92 300 1234567\n05 Apr 2026 y") == "Cell # <PHONE>\n05 Apr 2026 y"
    assert scrub_text("Phone +1 (415) 555-2671\n04/01/2026 | w") == "Phone <PHONE>\n04/01/2026 | w"
    assert scrub_text("Tel: +92-300-1234567 ok") == "Tel: <PHONE> ok"
