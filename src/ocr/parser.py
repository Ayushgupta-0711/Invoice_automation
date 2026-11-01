"""Conservative rule-based extraction of common invoice header fields."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,4})?"
_AMOUNT_TOKEN = re.compile(rf"(?:₹|Rs\.?\s*|INR\s*)?({_NUMBER})", re.IGNORECASE)
_DATE_TOKEN = re.compile(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4})")
_PO_TOKEN = re.compile(r"\bPO[- ]\d{4}[- ]\d{6}\b", re.IGNORECASE)


def parse_amount(value: Any) -> Decimal | None:
    """Parse a currency-like value into Decimal; return None for blank/invalid input."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    text = re.sub(r"^(?:₹|Rs\.?|INR)\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*(?:INR|Rs\.?)$", "", text, flags=re.IGNORECASE)
    text = text.strip()
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _label_value(text: str, labels: tuple[str, ...]) -> str | None:
    """Read a value only when a supported label starts the line.

    Anchoring avoids mistaking a Due Date line for the Invoice Date or an
    Invoice Date line for the supplier invoice number.
    """
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        for label in labels:
            match = re.match(rf"^{label}\s*(?:[:#=-]\s*|\s+)(.+)$", candidate, re.IGNORECASE)
            if match:
                return match.group(1).strip()
    return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    match = _DATE_TOKEN.search(value)
    if not match:
        return None
    token = match.group(1)
    formats = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y")
    for fmt in formats:
        try:
            return datetime.strptime(token, fmt).date()
        except ValueError:
            continue
    return None


def _parse_labeled_amount(text: str, labels: tuple[str, ...]) -> Decimal | None:
    value = _label_value(text, labels)
    if not value:
        return None
    match = _AMOUNT_TOKEN.search(value)
    return parse_amount(match.group(1)) if match else None


def _parse_line_items(text: str) -> list[dict[str, Any]]:
    """Parse only simple rows ending in quantity, unit price and line total.

    Uncertain rows are deliberately ignored and must be entered/reviewed by a user.
    """
    rows: list[dict[str, Any]] = []
    number = rf"(?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d{{1,4}})?"
    pattern = re.compile(rf"^(?P<description>.+?)\s+(?P<quantity>{number})\s+(?P<unit_price>{number})\s+(?P<line_total>{number})\s*$")
    ignored = ("subtotal", "taxable", "tax amount", "gst", "grand total", "invoice total", "amount due", "total payable")
    for line in text.splitlines():
        candidate = re.sub(r"\s+", " ", line.strip(" |\t"))
        if not candidate or any(word in candidate.lower() for word in ignored):
            continue
        match = pattern.match(candidate)
        if not match:
            continue
        qty = parse_amount(match.group("quantity"))
        price = parse_amount(match.group("unit_price"))
        description = match.group("description").strip(" -|:")
        if qty is None or price is None or qty <= 0 or not description or len(description) < 3:
            continue
        rows.append({
            "description": description[:255],
            "sku": "",
            "uom": "UNIT",
            "quantity": float(qty),
            "unit_price": float(price),
            "tax_code": "",
            "tax_rate": 0.0,
        })
    # Avoid likely header/noise matches and keep a manageable number of suggestions.
    return rows[:50]


def parse_invoice_text(text: str) -> dict[str, Any]:
    """Extract likely header fields. All returned values require human review."""
    invoice_number = _label_value(text, (
        r"supplier\s+invoice\s+(?:number|no\.?|#)",
        r"tax\s+invoice\s+(?:number|no\.?|#)",
        r"invoice\s+(?:number|no\.?|#)",
        r"bill\s+(?:number|no\.?|#)",
        r"document\s+(?:number|no\.?|#)",
    ))
    if invoice_number is None:
        # Also support a concise label such as `Invoice: ABC-123`, but do not
        # let it match `Invoice Date:` or other longer labels.
        simple_invoice = re.search(r"(?im)^\s*invoice\s*[:#=-]\s*(.+)$", text)
        invoice_number = simple_invoice.group(1).strip() if simple_invoice else None
    if invoice_number:
        invoice_number = re.split(r"\s{2,}|\s+(?:date|po\s+no)\b", invoice_number, maxsplit=1, flags=re.IGNORECASE)[0]
        invoice_number = invoice_number.strip(" .:#")[:100]

    invoice_date = _parse_date(_label_value(text, (r"invoice\s+date", r"bill\s+date", r"date")))
    due_date = _parse_date(_label_value(text, (r"due\s+date", r"payment\s+due\s+date")))
    po_match = _PO_TOKEN.search(text)

    subtotal = _parse_labeled_amount(text, (r"subtotal", r"sub\s+total", r"taxable\s+value", r"taxable\s+amount"))
    tax = _parse_labeled_amount(text, (r"total\s+tax", r"tax\s+amount", r"gst\s+amount", r"tax\s+total"))
    total = _parse_labeled_amount(text, (r"grand\s+total", r"invoice\s+total", r"total\s+amount", r"amount\s+due", r"total\s+payable", r"net\s+payable"))
    if total is None:
        total = _parse_labeled_amount(text, (r"total",))

    line_items = _parse_line_items(text)
    # A document-level effective tax rate is only a suggestion for line review.
    # Mixed tax rates must be corrected by the reviewer before saving.
    if subtotal is not None and tax is not None and subtotal > 0:
        inferred_rate = (tax * Decimal("100") / subtotal).quantize(Decimal("0.0001"))
        if Decimal("0") <= inferred_rate <= Decimal("100"):
            for item in line_items:
                item["tax_rate"] = float(inferred_rate)

    return {
        "supplier_invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "due_date": due_date,
        "po_reference": po_match.group(0).upper().replace(" ", "-") if po_match else None,
        "declared_subtotal": subtotal,
        "declared_tax": tax,
        "declared_total": total,
        "line_items": line_items,
        "raw_text": text,
    }
