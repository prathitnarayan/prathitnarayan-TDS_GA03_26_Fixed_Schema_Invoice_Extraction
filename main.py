import os
import json
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(
    api_key=os.environ["AIPIPE_TOKEN"],
    base_url="https://aipipe.org/openai/v1"
)


class InvoiceRequest(BaseModel):
    invoice_text: str


SYSTEM_PROMPT = """
You extract structured information from invoices.

Return ONLY valid JSON.

Return EXACTLY these keys:

{
  "invoice_no": string|null,
  "date": string|null,
  "vendor": string|null,
  "amount": number|null,
  "tax": number|null,
  "currency": string|null
}

Rules:

- invoice_no = invoice number/reference
- date = ISO YYYY-MM-DD
- vendor = seller/vendor/company name
- amount = subtotal BEFORE tax
- tax = ONLY tax amount
- currency = ISO code like INR USD EUR GBP JPY

If missing return null.

No markdown.
No explanation.
"""


def normalize_date(value):

    if value is None:
        return None

    if not isinstance(value, str):
        return None

    value = value.strip()

    formats = [
        "%Y-%m-%d",
        "%d %B %Y",
        "%d %b %Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(
                value,
                fmt
            ).strftime("%Y-%m-%d")
        except:
            pass

    return None


@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/extract")
def extract(req: InvoiceRequest):

    try:

        response = client.chat.completions.create(
            model="gpt-4.1",
            temperature=0,
            response_format={
                "type": "json_object"
            },
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": req.invoice_text
                }
            ]
        )

        result = json.loads(
            response.choices[0].message.content
        )

        output = {
            "invoice_no": None,
            "date": None,
            "vendor": None,
            "amount": None,
            "tax": None,
            "currency": None,
        }

        output.update(result)

        if output["date"]:
            output["date"] = normalize_date(
                output["date"]
            )

        if output["amount"] is not None:
            output["amount"] = float(output["amount"])

        if output["tax"] is not None:
            output["tax"] = float(output["tax"])

        if output["currency"]:
            output["currency"] = (
                output["currency"]
                .strip()
                .upper()
            )

        if output["vendor"]:
            output["vendor"] = (
                output["vendor"]
                .strip()
            )

        if output["invoice_no"]:
            output["invoice_no"] = (
                output["invoice_no"]
                .strip()
            )

        return output

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )