"""Deterministic transaction-line parser: the free, instant first stage of the hybrid analysis.

It reads only lines whose structure is unambiguous and hands everything else back
("leftover") so the model sees as little text as possible.  It never guesses:
no direction (sign, Cr/Dr, debit/credit column) -> the line is left over.

Recognised shapes (all generic, none tied to a bank or file name)
  1. header CSV     a header line with a date-like and an amount-like column, then rows
                    (wallet exports, PayPal exports; comma, semicolon or tab separated)
  2. pipe rows      "<date> | <description> | +/-<money> | <balance>"   (DOCX/PDF tables)
  3. Cr/Dr suffix   "<date> <description> PKR1,234.00Dr PKR9,999.00"    (bank PDFs)

Rows carry an `evidence` field (the source line) exactly like model rows, and are put
through the same verify_rows() check afterwards.
"""
import csv
import io
import re
from dataclasses import dataclass, field

MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
DATE_RE = re.compile(
    rf"\d{{4}}-\d{{2}}-\d{{2}}(?:[ T]\d{{2}}:\d{{2}}(?::\d{{2}})?)?"      # 2026-04-01 08:55:12
    rf"|\d{{1,2}}[ -]{MONTHS}[ -]\d{{4}}"                                    # 01 Apr 2026 / 01-Apr-2026
    rf"|\d{{1,2}}[/.]\d{{1,2}}[/.]\d{{2,4}}", re.I)                         # 04/01/2026, 01.04.2026
AMOUNT_LIKE = re.compile(r"\d[\d.,]*[.,]\d{2}(?!\d)")                        # a number with 2 decimals

SYMBOLS = {"C$": "CAD", "A$": "AUD", "US$": "USD", "£": "GBP", "$": "USD", "€": "EUR", "₹": "INR"}
_SYM = r"C\$|A\$|US\$|[£$€₹]"
_NUM = r"\d[\d.,]*"
MONEY_RE = re.compile(rf"^\(?\s*([+\-−–])?\s*(?:({_SYM}|[A-Z]{{3}})\s?)?({_NUM})\s*(?:([A-Z]{{3}}))?\s*\)?$")
SUFFIX_RE = re.compile(rf"(?:\b([A-Z]{{3}})\s?)?({_NUM}[.,]\d{{2}})\s?(Cr|Dr|CR|DR)\b")

DATE_NAMES = ("date", "time", "timestamp", "datum", "posted", "booking")
AMOUNT_NAMES = ("amount", "betrag", "value")
DEBIT_NAMES = ("debit", "withdrawal", "paid out", "money out")
CREDIT_NAMES = ("credit", "deposit", "paid in", "money in")
SKIP_NAMES = ("balance", "fee", "total", "status", "saldo")


BALANCE_LINE = re.compile(r"\bb/f\b|\bc/f\b|opening|closing|brought forward|carried forward|balance", re.I)


@dataclass
class ParseResult:
    rows: list = field(default_factory=list)
    leftover: list = field(default_factory=list)      # lines the parser could not read
    skipped_zero: int = 0                              # zero-amount lines (not transactions)


# ------------------------------------------------------------------ numbers
def to_amount(tok: str) -> float | None:
    """'1,234.56' / '1.234,56' / '1,12,340.00' / '64,35' / '61050.00' -> float. None if unreadable."""
    tok = tok.strip()
    if not re.fullmatch(_NUM, tok):
        return None
    last_dot, last_com = tok.rfind("."), tok.rfind(",")
    if last_dot >= 0 and last_com >= 0:                       # both: the later one is the decimal mark
        dec = "." if last_dot > last_com else ","
    elif last_com >= 0:                                       # comma only: decimal if 1-2 digits follow
        dec = "," if len(tok) - last_com - 1 in (1, 2) and tok.count(",") == 1 else None
    elif last_dot >= 0:                                       # dot only: decimal unless 1.234.567
        dec = "." if tok.count(".") == 1 else None
    else:
        dec = None
    if dec:
        whole, frac = tok[:tok.rfind(dec)], tok[tok.rfind(dec) + 1:]
        whole = re.sub(r"[.,]", "", whole)
        tok = f"{whole}.{frac}"
    else:
        tok = re.sub(r"[.,]", "", tok)
    try:
        return round(float(tok), 2)
    except ValueError:
        return None


def parse_money(cell: str):
    """A cell such as '-£54.20', '+C$3,120.00', '+$220.00 USD', '+PKR 61050.00', '-1240.50',
    '(45.00)'.  Returns (amount, direction|None, currency|None) or None if it is not money."""
    m = MONEY_RE.match(cell.strip())
    if not m:
        return None
    sign, cur1, num, cur2 = m.groups()
    amount = to_amount(num)
    if amount is None:
        return None
    cur = None
    for c in (cur1, cur2):
        if c:
            cur = SYMBOLS.get(c, c if re.fullmatch(r"[A-Z]{3}", c) else None) or cur
    direction = {"+": "credit", "-": "debit", "−": "debit", "–": "debit"}.get(sign or "")
    if direction is None and cell.strip().startswith("("):    # accounting negative
        direction = "debit"
    return amount, direction, cur


# ------------------------------------------------------------------ categories (keywords, no model)
_RULES = [  # first match wins; order matters (specific before general)
    ("refund_reversal", r"\brevers|\brefund|\bchargeback"),
    ("fees_taxes", r"\bcharges?\b|\bfees?\b|\btax\b|\bwht\b|\bfed\b|\bvat\b|service charge|council tax|\bgst\b|\bduty\b"),
    ("cash_withdrawal", r"\batm\b|cash withdraw|\bwdl\b|cash wdl|geldautomat"),
    ("rent_housing", r"\brent\b|\bmiete\b|\bmortgage\b|\blandlord\b"),
    ("transfer", r"transfer|\braast\b|\bibft\b|\bp2p\b|\bzelle\b|\bvenmo\b|e-transfer|faster payment|"
                 r"ueberweisung|überweisung|\bimps\b|\bneft\b|remit|payoneer|money (?:sent|received)|conversion|\bnote to self"),
    ("groceries", r"super ?market|grocer|\btesco\b|whole foods|loblaws|\brewe\b|instamart|\bmart\b|"
                  r"supermarkt|aldi|lidl|walmart|carrefour|\bcoles\b|woolworths|spar\b"),
    ("dining", r"foodpanda|starbucks|\bkfc\b|tim hortons|restaurant|zomato|swiggy|\bcafe\b|chai|"
              r"mcdonald|pizza|uber eats|deliveroo|doordash|burger|dominos|subway"),
    ("transport", r"careem|\buber\b|presto|\bbvg\b|fare\b|ticket|petrol|\bfuel\b|\btaxi\b|\bshell\b|"
                  r"\bmetro\b|railway|\bbus\b|parking|\btoll\b"),
    ("utilities", r"electric|energy|hydro|water|sewerage|stormfiber|internet|vodafone|\bgas\b|"
                  r"mseb|ptcl|utility|billpay|bill payment|top ?-?up|broadband|telecom|\bjazz\b|\bzong\b|\bufone\b"),
    ("subscriptions", r"netflix|spotify|github|namecheap|domain|subscription|youtube|disney|prime video|"
                      r"icloud|dropbox|adobe|openai|chatgpt"),
    ("entertainment", r"\bsteam\b|cinema|playstation|xbox|nintendo|concert|\bgames?\b"),
    ("shopping", r"amazon|daraz|ebay|aliexpress|shopping|\bmall\b|marketplace|mktplace|\bikea\b|zara"),
    ("health", r"pharmacy|hospital|clinic|doctor|medical|dental|health"),
    ("education", r"tuition|school|university|college|udemy|coursera|course"),
]
_CREDIT_INCOME = r"salary|payroll|gehalt|wages|dividend|interest|profit payment|deposit|\bgrid\b"
_COMPANY = r"\b(?:llc|ltd|inc|gmbh|pvt|plc|corp|co\.|ag|pty|limited)\b"


def categorize(description: str, direction: str) -> str:
    d = description.lower()
    if direction == "credit":
        if re.search(r"\brevers|\brefund|\bchargeback", d):
            return "refund_reversal"
        if re.search(_CREDIT_INCOME, d):
            return "income"
        if re.search(_COMPANY, d):                          # paid by a company: earnings
            return "income"
        if re.search(_RULES[4][1], d):
            return "transfer"
        return "other"
    for cat, pat in _RULES:
        if re.search(pat, d):
            return cat
    return "other"


_PREFIX = re.compile(
    r"^(?:paid to|payment to|pos purchase|direct debit|ach debit|payroll deposit|salary credit|salary|"
    r"sepa-lastschrift|sepa-ueberweisung an|faster payment to|zelle payment to|venmo payment to|"
    r"interac e-transfer (?:to|from)|online transfer to|incoming fund transfer from|outgoing fund transfer to|"
    r"money (?:sent to|received from)|refund from|cash withdrawn at|billpay|bill payment|utility bill|"
    r"neft cr|ibft cr|raast p2p ft - digital|raast in ft|atm wdl|pos|ach|check|cheque no)\s+", re.I)
_STOP = re.compile(r"\b(?:REF|Card|Trace|ACC|ACCT|SORT|IBAN|Nickname|Transaction|Invoice|Order|MANDATSREF|"
                   r"UTR|Consumer|Visa|VERWENDUNG|GLAEUBIGER-ID)\b|[|,(]|<", re.I)


_PREFIX_ANY = re.compile(r"\b(?:paid to|payment to|money (?:sent to|received from)|refund from|"
                         r"incoming fund transfer from|outgoing fund transfer to|cash withdrawn at)\s+", re.I)
_RAILS = re.compile(r"^(?:UPI|IMPS|NEFT|RTGS)/", re.I)


def guess_merchant(description: str) -> str | None:
    """Counterparty / merchant name from a description, best effort; None if nothing usable."""
    s = description.strip()
    if re.match(r"(?:check|cheque)\b", s, re.I):               # no counterparty is printed on a cheque line
        return None
    if _RAILS.match(s):                                       # UPI/P2M/<REF_ID>/ZOMATO ONLINE ORDER
        segs = [x for x in s.split("/")[1:] if x and not x.startswith("<") and not re.fullmatch(r"P2[A-Z]", x, re.I)]
        s = re.sub(r"^TO\s+", "", segs[0], flags=re.I) if segs else ""
    elif _PREFIX.match(s):
        s = _PREFIX.sub("", s, count=1)
    elif (m := _PREFIX_ANY.search(s)):                        # CSV rows start with the "type" column
        s = s[m.end():]
    s = _STOP.split(s, maxsplit=1)[0]
    words = [w for w in s.split() if not re.fullmatch(r"[#\d\-/:.]+", w)][:4]
    name = " ".join(words).strip(" -:/")
    return name[:60] if len(name) >= 3 else None


# ------------------------------------------------------------------ row builder
def _row(date: str, description: str, amount: float, direction: str, currency, evidence: str) -> dict:
    description = re.sub(r"\s+", " ", description).strip()
    return {"date": date, "description": description[:200], "merchant": guess_merchant(description),
            "amount": amount, "direction": direction, "currency": currency,
            "category": categorize(description, direction), "evidence": evidence.strip()}


# ------------------------------------------------------------------ shape 1: header CSV
def _delimiter(line: str) -> str | None:
    for d in (",", ";", "\t"):
        if len(next(csv.reader([line], delimiter=d, skipinitialspace=True), [])) >= 3:
            return d
    return None


def _header_columns(line: str):
    """If `line` is a CSV header with a date column and an amount (or debit+credit) column,
    return (delimiter, {role: index...}); else None."""
    d = _delimiter(line)
    if not d:
        return None
    cells = [c.strip().lower() for c in next(csv.reader([line], delimiter=d, skipinitialspace=True))]
    idx = {}
    for i, c in enumerate(cells):
        if any(c.startswith(n) for n in SKIP_NAMES):
            idx.setdefault("skip", set()).add(i); continue
        if "date" not in idx and any(n in c for n in DATE_NAMES):
            idx["date"] = i
        elif "amount" not in idx and any(c.startswith(n) or c == n for n in AMOUNT_NAMES):
            idx["amount"] = i
        elif "debit" not in idx and any(c.startswith(n) for n in DEBIT_NAMES):
            idx["debit"] = i
        elif "credit" not in idx and any(c.startswith(n) for n in CREDIT_NAMES):
            idx["credit"] = i
    if "date" in idx and ("amount" in idx or ("debit" in idx and "credit" in idx)):
        return d, idx
    return None


def _csv_row(line: str, d: str, idx: dict):
    cells = next(csv.reader([line], delimiter=d, skipinitialspace=True), [])
    need = [idx[k] for k in ("date", "amount", "debit", "credit") if k in idx]
    if not cells or max(need) >= len(cells):
        return None
    dm = DATE_RE.search(cells[idx["date"]])
    if not dm:
        return None
    if "amount" in idx:
        money = parse_money(cells[idx["amount"]])
    else:
        dr, cr = cells[idx["debit"]].strip(), cells[idx["credit"]].strip()
        pick = dr if dr and not cr else cr if cr and not dr else None
        money = parse_money(pick) if pick else None
        if money:
            money = (money[0], "debit" if pick is dr else "credit", money[2])
    if money and money[1] is None and money[0] == 0:           # unsigned 0.00: not a transaction
        money = (0.0, "debit", money[2])
    if not money or money[1] is None:
        return None
    used = set(need) | idx.get("skip", set())
    desc = " ".join(c.strip() for i, c in enumerate(cells) if i not in used and c.strip())
    return money, dm.group(0), desc


# ------------------------------------------------------------------ the parser
def parse_statement(text: str) -> ParseResult:
    out = ParseResult()
    header = None                                              # (delimiter, columns) while inside a CSV table
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            header = None
            continue
        h = _header_columns(line)
        if h and not DATE_RE.search(line):
            header = h
            continue
        parsed = None
        if header:
            r = _csv_row(line, *header)
            if r:
                (amount, direction, cur), date, desc = r
                parsed = (date, desc, amount, direction, cur)
        if parsed is None and "|" in line and DATE_RE.match(line):
            parts = [p.strip() for p in line.split("|")]
            for i in range(1, len(parts)):
                money = parse_money(parts[i])
                if money and money[1]:
                    parsed = (DATE_RE.match(parts[0]).group(0), " ".join(parts[1:i]), money[0], money[1], money[2])
                    break
        if parsed is None and DATE_RE.match(line):
            sm = SUFFIX_RE.search(line)
            if sm:
                dm = DATE_RE.match(line)
                amount = to_amount(sm.group(2))
                if amount is not None:
                    parsed = (dm.group(0), line[dm.end():sm.start()], amount,
                              "credit" if sm.group(3).lower() == "cr" else "debit", sm.group(1))
        if parsed:
            date, desc, amount, direction, cur = parsed
            if amount == 0:
                out.skipped_zero += 1                          # e.g. a fee line of 0.00: not a transaction
            else:
                out.rows.append(_row(date, desc, amount, direction, cur, line))
        elif DATE_RE.search(line) and AMOUNT_LIKE.search(line) and not BALANCE_LINE.search(line):
            out.leftover.append(line)                          # looks like a transaction; let the model read it
    return out
