"""Step 2: local PII scrubbing engine (regex + validators, no network).

Order matters: labelled fields -> emails/handles -> IBAN -> national IDs ->
cards -> bank codes -> phones -> reference/long IDs -> IP.
Anything we KEEP (names, merchants, dates, amounts, last-4) is never matched.
"""
import re

I = re.IGNORECASE

# ---------- helpers ----------
IBAN_LEN = {"PK": 24, "GB": 22, "DE": 22, "FR": 27, "ES": 24, "IT": 27, "NL": 18,
            "AE": 23, "SA": 24, "IE": 22, "BE": 16, "CH": 21, "TR": 26}
ISO_CC = set("""AD AE AF AL AM AR AT AU AZ BA BD BE BG BH BR BY CA CH CL CN CO CZ DE DK DZ EE EG ES FI FR
GB GE GR HK HR HU ID IE IL IN IQ IR IS IT JO JP KE KR KW KZ LB LK LT LU LV MA MD MT MX MY NG NL NO NP NZ
OM PE PH PK PL PT QA RO RS RU SA SE SG SI SK TH TN TR TW UA US UY UZ VN ZA""".split())


def luhn_ok(digits: str) -> bool:
    s, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        s += d
        alt = not alt
    return s % 10 == 0


def _join_wrapped_ibans(text: str) -> str:
    """PDF layout can wrap the tail of an IBAN onto the next line ('...567\\n02')."""
    def fix(m):
        head, tail = m.group(1), m.group(2)
        want = IBAN_LEN.get(head[:2].upper())
        return head + tail if want and len(head) + len(tail) == want else m.group(0)
    return re.sub(r"\b([A-Z]{2}\d{2}[A-Z0-9]{8,26})[ \t]*\n[ \t]*(\d{1,4})\b", fix, text)


def _sub(pattern, repl, text, flags=0):
    return re.sub(pattern, repl, text, flags=flags)


# ---------- rules ----------
def scrub_text(text: str) -> str:
    t = _join_wrapped_ibans(text)

    # 1. Labelled fields (label kept, value removed) -------------------------
    t = _sub(r"((?:customer\s+|billing\s+|home\s+|mailing\s+)?address|anschrift|adresse)"
             r"(\s*[:,]\s*)[^\n]+", r"\1\2<ADDRESS>", t, I)
    t = _sub(r"\b(wallet\s*id|upi\s*id|vpa|payment\s*handle)(\s*[:,]\s*)\S+",
             r"\1\2<PAYMENT_HANDLE>", t, I)
    t = _sub(r"\b(sort\s*code|sort)(\s*[:#]?\s*)\d{2}-?\d{2}-?\d{2}(\s*\(\d{6}\))?",
             r"\1\2<ROUTING_ID>", t, I)
    t = _sub(r"\b(routing(?:\s*(?:number|no\.?))?|aba|transit|institution|blz|micr|bsb)"
             r"(\s*[:,#]?\s*)\d[\d -]{1,}\d", r"\1\2<ROUTING_ID>", t, I)
    t = _sub(r"\b(ssn|sin|nin|tin|tax\s*id|steuer-?id|aadhaa?r|cnic|national\s*id)"
             r"((?:\s+on\s+file)?\s*[:,#]?\s*)\d[\d -]{6,}\d", r"\1\2<NATIONAL_ID>", t, I)
    t = _sub(r"(a/c\s*no\.?|acc(?:ount)?(?:\s*(?:no\.?|number|num|#))?|konto(?:nummer)?|"
             r"wallet\s*account\s*number|consumer(?:\s*(?:number|no\.?))?|customer\s*id)"
             r"(\s*[:,#]?\s*)\d[\d -]{3,}\d", r"\1\2<ACCOUNT_ID>", t, I)
    t = _sub(r"\bexpires?\s+\d{2}/\d{2,4}", "expires <CARD_EXPIRY>", t, I)

    # 2. Emails, then payment handles (handle = name@provider with NO dotted domain)
    t = _sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "<EMAIL>", t)
    t = _sub(r"[\w.+-]+@[A-Za-z][\w-]*\b(?!\.\w)", "<PAYMENT_HANDLE>", t)
    t = _sub(r"(?<![\w@])@[A-Za-z0-9][\w-]{2,}", "<PAYMENT_HANDLE>", t)  # @venmo-style

    # 3. IBAN (compact or space-grouped; case-insensitive) --------------------
    t = _sub(r"\b[A-Z]{2}\d{2}(?: [A-Z0-9]{4}){3,6}(?: \d{1,4})?\b", "<IBAN>", t, I)
    t = _sub(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b", lambda m: "<IBAN>" if re.search(r"\d", m.group()[4:]) and
             m.group()[:2].upper() in ISO_CC else m.group(), t, I)

    # 4. National IDs with a fixed shape ------------------------------------
    t = _sub(r"\b\d{5}-\d{7}-\d\b", "<NATIONAL_ID>", t)                       # PK CNIC
    t = _sub(r"\b\d{3}-\d{2}-\d{4}\b", "<NATIONAL_ID>", t)                    # US SSN
    t = _sub(r"\b(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b",
             "<NATIONAL_ID>", t)                                              # UK NINO
    t = _sub(r"\b[A-Z]{5}\d{4}[A-Z]\b", "<NATIONAL_ID>", t)                   # IN PAN
    t = _sub(r"(?<!\d[ -])\b[2-9]\d{3}[ -]\d{4}[ -]\d{4}\b(?![ -]\d)", "<NATIONAL_ID>", t)  # IN Aadhaar
    t = _sub(r"\b784-?\d{4}-?\d{7}-?\d\b", "<NATIONAL_ID>", t)                # UAE Emirates ID

    # 5. Cards ---------------------------------------------------------------
    t = _sub(r"\b\d{4,6}[ -]?[*Xx•]{4,}[ -]?\d{4}\b", "<CARD_NUMBER>", t)     # BIN visible
    t = _sub(r"\b(?:\d{4}[ -]){3}\d{4}\b",
             lambda m: "<CARD_NUMBER>" if luhn_ok(re.sub(r"\D", "", m.group())) else "<ACCOUNT_ID>", t)
    t = _sub(r"(?<![\d.,])(?=[3-6])\d{15,16}(?![\d]|[.,]\d)",
             lambda m: "<CARD_NUMBER>" if luhn_ok(m.group()) else m.group(), t)

    # 6. Bank codes: IFSC, SWIFT/BIC ------------------------------------------
    t = _sub(r"\b[A-Z]{4}0[A-Z0-9]{6}\b", "<ROUTING_ID>", t)                  # IFSC
    t = _sub(r"\b(bic|swift)(?:\s*code)?(\s*[:,]?\s*)[A-Z]{6}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b",
             r"\1\2<SWIFT_BIC>", t, I)
    t = _sub(r"\b[A-Z]{6}[A-Z0-9]{2}XXX\b", "<SWIFT_BIC>", t)                 # ...XXX form
    t = _sub(r"(?<=, )[A-Z]{4}([A-Z]{2})[A-Z0-9]{2}(?=[,\s])",                # ', SCBLPKKA,'
             lambda m: "<SWIFT_BIC>" if m.group(1) in ISO_CC else m.group(), t)

    # 7. Phones --------------------------------------------------------------
    def phone(m):
        s = m.group()
        digits = re.sub(r"\D", "", s)
        if re.fullmatch(r"\+\d+\.\d{2}", s) or not 8 <= len(digits) <= 15:
            return s  # looks like an amount such as +118300.00
        return "<PHONE>"
    t = _sub(r"(?<![\w.,])\+\d{1,3}(?:[\s.-]?(?:\(\d{1,5}\)|\d{1,5})){2,5}", phone, t)  # international
    t = _sub(r"(?<![\w.,])\(0\d{3}\)\s?\d{7}\b", "<PHONE>", t)                 # (0315) 1234567
    t = _sub(r"(?<![\w.,])0\d{3}[ -]?\d{7}\b", "<PHONE>", t)                   # PK local
    t = _sub(r"(?<![\w.,])0\d{4}[ ]?\d{6}\b", "<PHONE>", t)                    # UK local
    t = _sub(r"(?<![\w.,])\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b", "<PHONE>", t)    # US/CA
    t = _sub(r"(?<![\w.,])04\d{2}[ ]?\d{3}[ ]?\d{3}\b", "<PHONE>", t)          # AU mobile
    t = _sub(r"(?<![\w.,])\(0\d\)[ ]?\d{4}[ ]?\d{4}\b", "<PHONE>", t)          # AU landline

    # 8. Reference IDs ---------------------------------------------------------
    t = _sub(r"\b(ref(?:erence)?s?|mandatsref|order|invoice|trace|txn|utr|"
             r"transaction\s*ids?|acct)\b([\s#:.-]*)(?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{3,}",
             r"\1\2<REF_ID>", t, I)
    t = _sub(r"\b\d{3}-\d{7}-\d{7}\b", "<REF_ID>", t)                         # marketplace order no.
    t = _sub(r"\b[A-Z]{1,4}-\d{4,}-\d{4,}\b", "<REF_ID>", t)
    t = _sub(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{12,}\b", "<REF_ID>", t)
    t = _sub(r"(?<![\d.,])\d{7,}(?![\d]|[.,]\d)", "<REF_ID>", t)              # long numerics

    # 9. IP addresses ---------------------------------------------------------
    t = _sub(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", "<IP_ADDRESS>", t)
    return t
