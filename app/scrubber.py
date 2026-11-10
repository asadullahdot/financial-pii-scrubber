"""Step 2: the PII scrubbing engine.

Runs 100% locally: plain regular expressions plus a few validators.  No
network, no model, no external service.

HOW IT WORKS
    scrub_text(raw_text) applies a long list of rules, one after another.
    Each rule finds one kind of identifier and replaces it with a placeholder
    such as <IBAN> or <PHONE>.

WHY THE ORDER OF THE RULES MATTERS
    Rules run from MOST specific to MOST generic.  If a generic rule ran first
    it would grab pieces of things a specific rule understands better.
    Example: the long-number rule would swallow the digits of a phone number
    before the phone rule could label it <PHONE>.

        1. labelled fields   ("Address: ...", "Account No: ...")
        2. emails / payment handles
        3. IBANs
        4. national IDs      (CNIC, SSN, UK NI, PAN, Aadhaar, Emirates ID)
        5. card numbers
        6. bank codes        (IFSC, SWIFT/BIC)
        7. phone numbers
        8. reference IDs and long numbers
        9. IP addresses

WHAT IS DELIBERATELY *NOT* TOUCHED  (it carries analytical value)
    person names, merchant names (even "Netflix.com"), dates, times,
    amounts, balances, currencies, masked last-4 digits (xxxx8821).
    There is intentionally NO "remove URLs/domains" rule, because that would
    destroy merchants like Netflix.com or Daraz.pk.
"""
import re

# Shorthand: make a pattern case-insensitive ("Address" == "ADDRESS").
I = re.IGNORECASE


# ==========================================================================
# LOOKUP TABLES AND HELPER FUNCTIONS
# ==========================================================================

# Total length of a valid IBAN per country.  Used ONLY to repair IBANs that a
# PDF wrapped onto two lines (see _join_wrapped_ibans).
IBAN_LEN = {"PK": 24, "GB": 22, "DE": 22, "FR": 27, "ES": 24, "IT": 27, "NL": 18,
            "AE": 23, "SA": 24, "IE": 22, "BE": 16, "CH": 21, "TR": 26}

# Real ISO country codes.  Lets us tell the SWIFT code "SCBLPKKA" (letters 5-6
# = "PK", a country) from an ordinary English word such as "RESOLVED".
ISO_CC = set("""AD AE AF AL AM AR AT AU AZ BA BD BE BG BH BR BY CA CH CL CN CO CZ DE DK DZ EE EG ES FI FR
GB GE GR HK HR HU ID IE IL IN IQ IR IS IT JO JP KE KR KW KZ LB LK LT LU LV MA MD MT MX MY NG NL NO NP NZ
OM PE PH PK PL PT QA RO RS RU SA SE SG SI SK TH TN TR TW UA US UY UZ VN ZA""".split())


def luhn_ok(digits: str) -> bool:
    """Luhn checksum: every real credit/debit card number passes it.

    Used so we only call a long digit string a <CARD_NUMBER> when it is
    mathematically a valid card number, not just any 16 digits.
    """
    total, double = 0, False
    for ch in reversed(digits):          # walk from the last digit backwards
        d = int(ch)
        if double:                       # double every second digit...
            d = d * 2 - 9 if d * 2 > 9 else d * 2   # ...subtracting 9 if > 9
        total += d
        double = not double
    return total % 10 == 0


def _join_wrapped_ibans(text: str) -> str:
    """Re-attach an IBAN whose last digits wrapped onto the next line.

    PDFs sometimes break a long IBAN across two lines, e.g.
        IBAN: PK36SCBL00000011234567
        02
    Neither half looks like an IBAN alone, so it would leak.  We join the two
    parts, but ONLY if the combined length equals the IBAN length for that
    country, so unrelated numbers on the next line are never swallowed.
    """
    def fix(m):
        head, tail = m.group(1), m.group(2)
        want = IBAN_LEN.get(head[:2].upper())
        # join only if the result is exactly a full-length IBAN
        return head + tail if want and len(head) + len(tail) == want else m.group(0)

    # group 1: IBAN start (2 letters, 2 digits, more chars)   group 2: 1-4 digits
    # sitting alone at the start of the next line
    return re.sub(r"\b([A-Z]{2}\d{2}[A-Z0-9]{8,26})[ \t]*\n[ \t]*(\d{1,4})\b", fix, text)


def _sub(pattern, repl, text, flags=0):
    """Tiny wrapper around re.sub so every rule below fits on a few lines."""
    return re.sub(pattern, repl, text, flags=flags)


# ==========================================================================
# THE SCRUBBER
# ==========================================================================
def scrub_text(text: str) -> str:
    """Return `text` with every identifier replaced by a <PLACEHOLDER>."""

    # Step 0: repair wrapped IBANs first so rule 3 can see them whole.
    t = _join_wrapped_ibans(text)

    # ----------------------------------------------------------------------
    # RULE 1: LABELLED FIELDS
    # The label tells us what the value is, so we can remove values that have
    # no recognisable shape (an address) or an unusual shape (an account
    # number).  The label itself is KEPT so the text still reads naturally:
    #     "Customer Address: House 5, Karachi"  ->  "Customer Address: <ADDRESS>"
    # \1 and \2 in the replacement re-insert the label and its separator.
    # ----------------------------------------------------------------------

    # Addresses: remove the whole rest of the line after the label.
    t = _sub(r"((?:customer\s+|billing\s+|home\s+|mailing\s+)?address|anschrift|adresse)"
             r"(\s*[:,]\s*)[^\n]+", r"\1\2<ADDRESS>", t, I)

    # Wallet / UPI IDs given as "Wallet ID: xyz" or "Wallet ID,xyz" (CSV).
    t = _sub(r"\b(wallet\s*id|upi\s*id|vpa|payment\s*handle)(\s*[:,]\s*)\S+",
             r"\1\2<PAYMENT_HANDLE>", t, I)

    # UK sort code, with an optional second copy in brackets: "40-22-19 (402219)".
    t = _sub(r"\b(sort\s*code|sort)(\s*[:#]?\s*)\d{2}-?\d{2}-?\d{2}(\s*\(\d{6}\))?",
             r"\1\2<ROUTING_ID>", t, I)

    # Other routing identifiers: US routing/ABA, CA transit + institution,
    # DE BLZ, MICR, AU BSB.  Value must contain at least 3 digits.
    t = _sub(r"\b(routing(?:\s*(?:number|no\.?))?|aba|transit|institution|blz|micr|bsb)"
             r"(\s*[:,#]?\s*)\d[\d -]{1,}\d", r"\1\2<ROUTING_ID>", t, I)

    # National IDs introduced by a label: SSN, SIN, Steuer-ID, Aadhaar, ...
    # "(?:\s+on\s+file)?" allows text like "SSN on file: 078-05-1120".
    t = _sub(r"\b(ssn|sin|nin|tin|tax\s*id|steuer-?id|aadhaa?r|cnic|national\s*id)"
             r"((?:\s+on\s+file)?\s*[:,#]?\s*)\d[\d -]{6,}\d", r"\1\2<NATIONAL_ID>", t, I)

    # Account numbers: "A/C No", "Account Number", "ACC", "Kontonummer",
    # "Consumer Number", "Customer ID", ...  The value must START with a digit,
    # so "Account Holder: Bilal" is not touched (names must survive).
    t = _sub(r"(a/c\s*no\.?|acc(?:ount)?(?:\s*(?:no\.?|number|num|#))?|konto(?:nummer)?|"
             r"wallet\s*account\s*number|consumer(?:\s*(?:number|no\.?))?|customer\s*id)"
             r"(\s*[:,#]?\s*)\d[\d -]{3,}\d", r"\1\2<ACCOUNT_ID>", t, I)

    # Card expiry date: "expires 09/28".
    t = _sub(r"\bexpires?\s+\d{2}/\d{2,4}", "expires <CARD_EXPIRY>", t, I)

    # ----------------------------------------------------------------------
    # RULE 2: EMAILS AND PAYMENT HANDLES
    # Order matters: an email has a dotted domain (name@gmail.com); a payment
    # handle does not (name@easywallet, name@okaxis).  Emails first, then
    # whatever "@word" is left must be a handle.
    # ----------------------------------------------------------------------
    t = _sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "<EMAIL>", t)
    t = _sub(r"[\w.+-]+@[A-Za-z][\w-]*\b(?!\.\w)", "<PAYMENT_HANDLE>", t)
    # Venmo/Cash-App style handle with no name part: "@carlos-r-tx"
    t = _sub(r"(?<![\w@])@[A-Za-z0-9][\w-]{2,}", "<PAYMENT_HANDLE>", t)

    # ----------------------------------------------------------------------
    # RULE 3: IBAN
    # Format: 2 letters (country) + 2 check digits + up to 30 letters/digits.
    # Two forms: spaced groups ("GB29 NWBK 6016 1331 9268 19") and compact
    # ("PK36SCBL0000001123456702").  Case-insensitive ("de89 3704 ...").
    # ----------------------------------------------------------------------
    t = _sub(r"\b[A-Z]{2}\d{2}(?: [A-Z0-9]{4}){3,6}(?: \d{1,4})?\b", "<IBAN>", t, I)
    # Compact form: also require a real country code and at least one digit
    # after the check digits, to avoid matching ordinary words.
    t = _sub(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b",
             lambda m: "<IBAN>" if re.search(r"\d", m.group()[4:]) and
             m.group()[:2].upper() in ISO_CC else m.group(), t, I)

    # ----------------------------------------------------------------------
    # RULE 4: NATIONAL IDs WITH A FIXED SHAPE
    # These have a unique format, so no label is needed.
    # ----------------------------------------------------------------------
    t = _sub(r"\b\d{5}-\d{7}-\d\b", "<NATIONAL_ID>", t)       # Pakistan CNIC: 42201-7654321-3
    t = _sub(r"\b\d{3}-\d{2}-\d{4}\b", "<NATIONAL_ID>", t)    # US SSN: 078-05-1120
    # UK National Insurance: 2 letters, 6 digits, suffix A-D.  The first group
    # excludes letter pairs the UK never issues (BG, GB, NK, KN, TN, NT, ZZ).
    t = _sub(r"\b(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b",
             "<NATIONAL_ID>", t)
    t = _sub(r"\b[A-Z]{5}\d{4}[A-Z]\b", "<NATIONAL_ID>", t)   # India PAN: ABCPD1234F
    # India Aadhaar: 3 groups of 4 digits.  The look-around guards make sure it
    # is NOT the first/last 12 digits of a 16-digit card number.
    t = _sub(r"(?<!\d[ -])\b[2-9]\d{3}[ -]\d{4}[ -]\d{4}\b(?![ -]\d)", "<NATIONAL_ID>", t)
    t = _sub(r"\b784-?\d{4}-?\d{7}-?\d\b", "<NATIONAL_ID>", t)  # UAE Emirates ID: 784-YYYY-NNNNNNN-C

    # ----------------------------------------------------------------------
    # RULE 5: CARD NUMBERS
    # A masked last-4 such as "xxxx8821" or "****6677" is ALLOWED to stay.
    # But a card showing its first 6 digits (the BIN) is a leak.
    # ----------------------------------------------------------------------
    # Partly masked with leading digits visible: "521234******2070"
    t = _sub(r"\b\d{4,6}[ -]?[*Xx•]{4,}[ -]?\d{4}\b", "<CARD_NUMBER>", t)
    # 4 groups of 4 digits ("4111 1111 1111 1111"): <CARD_NUMBER> if Luhn-valid,
    # otherwise it is more likely a bank account number.
    t = _sub(r"\b(?:\d{4}[ -]){3}\d{4}\b",
             lambda m: "<CARD_NUMBER>" if luhn_ok(re.sub(r"\D", "", m.group())) else "<ACCOUNT_ID>", t)
    # 15-16 plain digits starting with 3-6 (real card prefixes) AND Luhn-valid.
    # The (?<![\d.,]) / (?![\d]|[.,]\d) guards stop it matching inside amounts.
    t = _sub(r"(?<![\d.,])(?=[3-6])\d{15,16}(?![\d]|[.,]\d)",
             lambda m: "<CARD_NUMBER>" if luhn_ok(m.group()) else m.group(), t)

    # ----------------------------------------------------------------------
    # RULE 6: BANK CODES (IFSC, SWIFT/BIC)
    # ----------------------------------------------------------------------
    # India IFSC: 4 letters, the digit 0, then 6 letters/digits (SBIN0011223).
    t = _sub(r"\b[A-Z]{4}0[A-Z0-9]{6}\b", "<ROUTING_ID>", t)
    # SWIFT/BIC with a label: "BIC: DEUTDEFF", "SWIFT code CHASUS33XXX".
    t = _sub(r"\b(bic|swift)(?:\s*code)?(\s*[:,]?\s*)[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b",
             r"\1\2<SWIFT_BIC>", t, I)
    # 11-character form ending in XXX (a very distinctive SWIFT ending).
    t = _sub(r"\b[A-Z]{6}[A-Z0-9]{2}XXX\b", "<SWIFT_BIC>", t)
    # Unlabelled 8-letter code that follows ", " (e.g. "..., SCBLPKKA,").
    # Any 8-letter uppercase word *looks* like a BIC, so we also require letters
    # 5-6 to be a real country code.  "RESOLVED" would pass that test alone,
    # which is why we additionally demand the ", " in front.
    t = _sub(r"(?<=, )[A-Z]{4}([A-Z]{2})[A-Z0-9]{2}(?=[,\s])",
             lambda m: "<SWIFT_BIC>" if m.group(1) in ISO_CC else m.group(), t)

    # ----------------------------------------------------------------------
    # RULE 7: PHONE NUMBERS  (many countries, many formats)
    # Phones come BEFORE the long-number rule so they get the right label.
    # (?<![\w.,]) means "not glued to a preceding word, dot or comma", which
    # keeps us from matching the middle of an amount like 1,234.5678.
    # ----------------------------------------------------------------------
    def phone(m):
        s = m.group()
        digits = re.sub(r"\D", "", s)
        # "+118300.00" matches the shape of an international number but is an
        # amount.  Reject it, and anything outside 8-15 digits (the real range).
        if re.fullmatch(r"\+\d+\.\d{2}", s) or not 8 <= len(digits) <= 15:
            return s
        return "<PHONE>"

    # International: +92-300-1234567, +44 7700 900123, +1 (512) 555-0148
    t = _sub(r"(?<![\w.,])\+\d{1,3}(?:[\s.-]?(?:\(\d{1,5}\)|\d{1,5})){2,5}", phone, t)
    t = _sub(r"(?<![\w.,])\(0\d{3}\)\s?\d{7}\b", "<PHONE>", t)    # PK: (0315) 1234567
    t = _sub(r"(?<![\w.,])0\d{3}[ -]?\d{7}\b", "<PHONE>", t)      # PK: 0300-1234567
    t = _sub(r"(?<![\w.,])0\d{4}[ ]?\d{6}\b", "<PHONE>", t)       # UK: 07700 900123
    t = _sub(r"(?<![\w.,])\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b", "<PHONE>", t)  # US/CA: 416-555-0177
    t = _sub(r"(?<![\w.,])04\d{2}[ ]?\d{3}[ ]?\d{3}\b", "<PHONE>", t)        # AU mobile: 0412 345 678
    t = _sub(r"(?<![\w.,])\(0\d\)[ ]?\d{4}[ ]?\d{4}\b", "<PHONE>", t)        # AU landline: (02) 9374 4000

    # ----------------------------------------------------------------------
    # RULE 8: REFERENCE / TRACE / TRANSACTION IDs
    # Strongest signal first (a label), weakest last (just "a long number").
    # ----------------------------------------------------------------------
    # Word like REF / ORDER / TRACE / INVOICE / TXN followed by a value that
    # contains at least one digit.  (?=[A-Za-z0-9-]*\d) is that "has a digit"
    # check; it stops "REF RENT SHARE" from losing the word RENT.
    t = _sub(r"\b(ref(?:erence)?s?|mandatsref|order|invoice|trace|txn|utr|"
             r"transaction\s*ids?|acct)\b([\s#:.-]*)(?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{3,}",
             r"\1\2<REF_ID>", t, I)
    t = _sub(r"\b\d{3}-\d{7}-\d{7}\b", "<REF_ID>", t)            # marketplace order no: 402-8871253-1123456
    t = _sub(r"\b[A-Z]{1,4}-\d{4,}-\d{4,}\b", "<REF_ID>", t)     # P-114488-202600, SR-20260501-000341
    # Long mixed letters+digits token (12+ chars): PayPal IDs like 8XJ22190T7002841M
    t = _sub(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{12,}\b", "<REF_ID>", t)
    # Last resort: any run of 7+ plain digits.  The guards skip digits that are
    # part of an amount ("1,234,567.89" or "12345678.00").
    t = _sub(r"(?<![\d.,])\d{7,}(?![\d]|[.,]\d)", "<REF_ID>", t)

    # ----------------------------------------------------------------------
    # RULE 9: IP ADDRESSES  (four numbers 0-255 separated by dots)
    # ----------------------------------------------------------------------
    t = _sub(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", "<IP_ADDRESS>", t)
    return t
