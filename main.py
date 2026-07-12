import re
from datetime import datetime
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dateutil import parser as dateparser

app = FastAPI()

# Rule 4: CORS must be enabled (grader calls from a Cloudflare Worker)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class InvoiceIn(BaseModel):
    invoice_text: str


def clean_number(s: str) -> Optional[float]:
    if s is None:
        return None
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def extract_invoice_no(text: str) -> Optional[str]:
    patterns = [
        r"(?:Invoice\s*(?:No\.?|Number|#)\s*[:\-]?\s*)([A-Za-z0-9\-\/]+)",
        r"(?:Order\s*(?:No\.?|#)\s*[:\-]?\s*)([A-Za-z0-9\-\/]+)",
        r"(?:Bill\s*(?:No\.?|#)\s*[:\-]?\s*)([A-Za-z0-9\-\/]+)",
        r"(?:Ref(?:erence)?\s*(?:No\.?|#)\s*[:\-]?\s*)([A-Za-z0-9\-\/]+)",
        r"#\s*([A-Za-z0-9\-\/]+)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip(".,")
    return None


def extract_date(text: str) -> Optional[str]:
    # Look for explicit "Date:" style labels first
    label_patterns = [
        r"(?:Invoice\s*Date|Order\s*Date|Date|Dated|Purchase\s*Date|Billed\s*on)\s*[:\-]?\s*([A-Za-z0-9,\.\/\- ]{6,25})",
    ]
    candidates = []
    for p in label_patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            candidates.append(m.group(1).strip())

    # Fallback: any recognizable date-looking substring
    generic_date = re.search(
        r"(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4}|"
        r"[A-Za-z]+\s+\d{1,2},?\s+\d{4}|"
        r"\d{4}-\d{2}-\d{2}|"
        r"\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4})",
        text,
    )
    if generic_date:
        candidates.append(generic_date.group(1).strip())

    for cand in candidates:
        cand = re.sub(r"(st|nd|rd|th)\b", "", cand, flags=re.IGNORECASE).strip()
        cand = cand.rstrip(".,")
        try:
            dt = dateparser.parse(cand, dayfirst=True, fuzzy=True)
            if dt:
                return dt.strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            continue
    return None


def extract_vendor(text: str) -> Optional[str]:
    patterns = [
        r"(?:Vendor|Seller|Company|Merchant|Billed\s*by|Sold\s*by|From)\s*[:\-]\s*([A-Za-z0-9&.,'\- ]+?)(?:\n|$)",
        r"(?:from|at)\s+([A-Z][A-Za-z0-9&.,'\- ]+?(?:Store|Pvt\.?\s*Ltd\.?|Ltd\.?|LLC|Inc\.?|Enterprises|Traders))",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            vendor = m.group(1).strip().rstrip(".,")
            if vendor:
                return vendor
    return None


def extract_amount_and_tax(text: str):
    def find(label_alts):
        pattern = (
            r"(?:" + "|".join(label_alts) + r")"
            r"(?:\s*\([^)]*\))?\s*[:\-]?\s*"
            r"(?:Rs\.?|INR|\$|USD|€|EUR)?\s*"
            r"([\d,]+(?:\.\d+)?)"
        )
        m = re.search(pattern, text, re.IGNORECASE)
        return clean_number(m.group(1)) if m else None

    subtotal = find([r"Sub\s*[- ]?Total", r"Amount(?!\s*Due)"])
    tax = find([r"GST", r"Tax", r"VAT"])
    total = find([r"Grand\s*Total", r"TOTAL", r"Total\s*Amount"])

    if subtotal is None and total is not None and tax is not None:
        subtotal = round(total - tax, 2)
    if tax is None and subtotal is not None and total is not None:
        tax = round(total - subtotal, 2)
    if subtotal is None and tax is None and total is not None:
        subtotal = total
        tax = 0.0

    return subtotal, tax


def extract_currency(text: str) -> str:
    if re.search(r"Rs\.?|INR|₹", text, re.IGNORECASE):
        return "INR"
    if re.search(r"\$|USD", text):
        return "USD"
    if re.search(r"€|EUR", text, re.IGNORECASE):
        return "EUR"
    if re.search(r"£|GBP", text, re.IGNORECASE):
        return "GBP"
    return "INR"


@app.post("/extract")
def extract(payload: InvoiceIn):
    text = payload.invoice_text

    amount, tax = extract_amount_and_tax(text)

    return {
        "invoice_no": extract_invoice_no(text),
        "date": extract_date(text),
        "vendor": extract_vendor(text),
        "amount": amount,
        "tax": tax,
        "currency": extract_currency(text),
    }


@app.get("/")
def root():
    return {"status": "ok"}