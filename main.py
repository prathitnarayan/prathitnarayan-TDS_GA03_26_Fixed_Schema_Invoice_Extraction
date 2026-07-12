from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import re

app = FastAPI()

# CORS must be enabled — grader calls from a Cloudflare Worker (different origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MONTHS = {
    'jan': 1, 'january': 1, 'feb': 2, 'february': 2, 'mar': 3, 'march': 3,
    'apr': 4, 'april': 4, 'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
    'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9, 'oct': 10,
    'october': 10, 'nov': 11, 'november': 11, 'dec': 12, 'december': 12,
}


class InvoiceRequest(BaseModel):
    invoice_text: str


def parse_number(raw):
    """Handles Indian-style comma grouping (1,40,000.00) and Rs./currency prefixes."""
    if raw is None:
        return None
    s = re.sub(r'[^0-9.,]', '', raw)
    s = s.replace(',', '')
    if not s or s == '.':
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def extract_invoice_no(text):
    patterns = [
        r'(?:Invoice\s*(?:No\.?|Number|#)|Inv\.?\s*No\.?)\s*[:\-]?\s*([A-Za-z0-9\-/\.]+)',
        r'Ref(?:erence)?\.?\s*(?:No\.?)?\s*[:\-]?\s*([A-Za-z0-9\-/\.]+)',
        r'Bill\s*No\.?\s*[:\-]?\s*([A-Za-z0-9\-/\.]+)',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip('.')
    return None


def parse_date_token(raw):
    raw = raw.strip()
    m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', raw)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.match(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', raw)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.match(r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', raw)
    if m:
        d = int(m.group(1))
        mon = MONTHS.get(m.group(2).lower())
        y = int(m.group(3))
        if mon:
            return f"{y:04d}-{mon:02d}-{d:02d}"
    m = re.match(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', raw)
    if m:
        mon = MONTHS.get(m.group(1).lower())
        d = int(m.group(2))
        y = int(m.group(3))
        if mon:
            return f"{y:04d}-{mon:02d}-{d:02d}"
    return None


def extract_date(text):
    m = re.search(
        r'(?:Invoice\s*Date|Date|Issued|Dated)\s*[:\-]?\s*([0-9A-Za-z,/\-\s]+?)(?:\n|$)',
        text, re.IGNORECASE,
    )
    if m:
        return parse_date_token(m.group(1))
    return None


def extract_vendor(text):
    patterns = [
        r'Vendor\s*[:\-]?\s*(.+)',
        r'Sold\s*By\s*[:\-]?\s*(.+)',
        r'Seller\s*[:\-]?\s*(.+)',
        r'From\s*[:\-]?\s*(.+)',
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).strip().split('\n')[0].strip()
    # Fallback: many invoices lead with "<Vendor Name> — Tax Invoice" / "<Vendor Name> - Invoice"
    first_line = text.strip().split('\n')[0]
    m = re.match(r'^(.{2,60}?)\s*(?:—|--)\s*(?:Tax\s*)?Invoice\s*$', first_line, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def extract_amount_tax(text):
    subtotal = tax = total = None

    m = re.search(r'Sub[\s\-]?total\s*[:\-]?\s*[^\n\d]*([\d,]+\.?\d*)', text, re.IGNORECASE)
    if m:
        subtotal = parse_number(m.group(1))
    if subtotal is None:
        m = re.search(r'Amount(?!\s*Due)\s*[:\-]?\s*[^\n\d]*([\d,]+\.?\d*)', text, re.IGNORECASE)
        if m:
            subtotal = parse_number(m.group(1))

    m = re.search(
        r'(?:IGST|CGST|SGST|GST|VAT|Sales\s*Tax)\s*(?:\([\d.]+%\))?\s*[:\-]?\s*[^\n\d]*([\d,]+\.?\d*)',
        text, re.IGNORECASE,
    )
    if not m:
        # generic "Tax" label — require a colon/dash right after to avoid matching
        # incidental phrases like "Tax Invoice" in a header
        m = re.search(r'\bTax\s*(?:\([\d.]+%\))?\s*[:\-]\s*[^\n\d]*([\d,]+\.?\d*)', text, re.IGNORECASE)
    if m:
        tax = parse_number(m.group(1))

    m = re.search(r'(?:Total\s*Due|Grand\s*Total|Amount\s*Due|TOTAL)\s*[:\-]?\s*[^\n\d]*([\d,]+\.?\d*)', text, re.IGNORECASE)
    if m:
        total = parse_number(m.group(1))

    if subtotal is None and total is not None and tax is not None:
        subtotal = round(total - tax, 2)
    if tax is None and total is not None and subtotal is not None:
        tax = round(total - subtotal, 2)

    return subtotal, tax


def extract_currency(text):
    m = re.search(r'Currency\s*[:\-]?\s*([A-Za-z]{3})', text, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    if re.search(r'Rs\.|₹|INR', text):
        return "INR"
    if re.search(r'\$|USD', text):
        return "USD"
    if re.search(r'€|EUR', text):
        return "EUR"
    if re.search(r'£|GBP', text):
        return "GBP"
    return None


@app.get("/")
def root():
    return {"status": "ok"}


@app.post("/extract")
def extract(req: InvoiceRequest):
    text = req.invoice_text
    subtotal, tax = extract_amount_tax(text)
    return {
        "invoice_no": extract_invoice_no(text),
        "date": extract_date(text),
        "vendor": extract_vendor(text),
        "amount": subtotal,
        "tax": tax,
        "currency": extract_currency(text),
    }