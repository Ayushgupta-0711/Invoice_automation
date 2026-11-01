"""Persist a user-reviewed invoice upload and its extracted line items."""
from __future__ import annotations

import hashlib
import math
import uuid
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from sqlalchemy import text

from database.connection import get_engine
from src.core.config import ROOT_DIR

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
CENT = Decimal("0.01")


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(math.isnan(value))
    except (TypeError, ValueError):
        return False


def _decimal(value: Any, default: str = "0") -> Decimal:
    if _is_missing(value) or str(value).strip() == "":
        return Decimal(default)
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Numeric values must be finite numbers.")
    return result


def _clean_text(value: Any) -> str:
    return "" if _is_missing(value) else str(value).strip()


def _money(value: Any) -> Decimal:
    return _decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _relative_upload_path(file_bytes: bytes, filename: str) -> tuple[str, str, Path]:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("Upload must be a PDF or supported image (PNG, JPG, TIFF or BMP).")
    if not file_bytes:
        raise ValueError("The uploaded file is empty.")
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("The upload exceeds the 15 MB limit.")
    if suffix == ".pdf" and not file_bytes.startswith(b"%PDF-"):
        raise ValueError("The file extension is PDF, but the content is not a valid PDF document.")

    digest = hashlib.sha256(file_bytes).hexdigest()
    now = datetime.now()
    relative = Path("data") / "raw" / str(now.year) / f"{now.month:02d}" / f"{digest}{suffix}"
    absolute = ROOT_DIR / relative
    absolute.parent.mkdir(parents=True, exist_ok=True)
    if not absolute.exists():
        absolute.write_bytes(file_bytes)
    return relative.as_posix(), digest, absolute


def list_vendors() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(text("""SELECT vendor_id, vendor_code, vendor_name, vendor_status
                                     FROM vendors ORDER BY vendor_name""")).mappings().all()
    return [dict(row) for row in rows]


def list_tax_codes() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(text("""SELECT tax_code, tax_name, tax_rate
                                     FROM tax_codes WHERE is_active = 1 ORDER BY tax_rate""")).mappings().all()
    return [dict(row) for row in rows]


def list_users() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(text("""SELECT user_id, full_name, role
                                     FROM users WHERE is_active = 1 ORDER BY full_name""")).mappings().all()
    return [dict(row) for row in rows]


def list_vendor_purchase_orders(vendor_id: str) -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(text("""SELECT po_id, po_date, po_status
                                     FROM purchase_orders
                                     WHERE vendor_id = :vendor_id
                                     ORDER BY po_date DESC"""), {"vendor_id": vendor_id}).mappings().all()
    return [dict(row) for row in rows]


def find_possible_duplicates(vendor_id: str, supplier_invoice_number: str | None, file_hash: str) -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(text("""SELECT invoice_id, supplier_invoice_number, invoice_date,
                                         calculated_total, source_file_hash
                                  FROM invoices
                                  WHERE vendor_id = :vendor_id
                                    AND ((:number IS NOT NULL AND supplier_invoice_number = :number)
                                         OR source_file_hash = :file_hash)
                                  ORDER BY received_at ASC"""), {
            "vendor_id": vendor_id,
            "number": supplier_invoice_number or None,
            "file_hash": file_hash,
        }).mappings().all()
    return [dict(row) for row in rows]


def _next_invoice_id(conn: Any, vendor_id: str, invoice_year: int) -> str:
    """Generate a readable ID while serializing sequences per vendor/year."""
    vendor = conn.execute(
        text("SELECT vendor_id FROM vendors WHERE vendor_id = :vendor_id FOR UPDATE"),
        {"vendor_id": vendor_id},
    ).first()
    if not vendor:
        raise ValueError(f"Unknown vendor: {vendor_id}")
    compact_vendor = vendor_id.replace("-", "")
    prefix = f"INV-{compact_vendor}-{invoice_year}-"
    rows = conn.execute(
        text("SELECT invoice_id FROM invoices WHERE invoice_id LIKE :pattern"),
        {"pattern": prefix + "%"},
    ).scalars().all()
    sequences = []
    for existing_id in rows:
        try:
            sequences.append(int(str(existing_id).rsplit("-", 1)[1]))
        except (IndexError, ValueError):
            continue
    return f"{prefix}{max(sequences, default=0) + 1:06d}"


def save_reviewed_invoice(
    *,
    file_bytes: bytes,
    filename: str,
    vendor_id: str,
    supplier_invoice_number: str,
    invoice_date: date,
    due_date: date | None,
    po_id: str | None,
    currency_code: str,
    declared_subtotal: Any,
    declared_tax: Any,
    declared_total: Any,
    lines: list[dict[str, Any]],
    submitted_by: str | None,
    extraction_succeeded: bool,
    non_po_codings: list[dict[str, Any]] | None = None,
    raw_text: str = "",
) -> dict[str, Any]:
    """Save a human-reviewed invoice, document, lines and detected exceptions."""
    supplier_invoice_number = supplier_invoice_number.strip()
    if not supplier_invoice_number:
        raise ValueError("Supplier invoice number is required.")
    if not invoice_date:
        raise ValueError("Invoice date is required.")
    if not lines:
        raise ValueError("Add at least one invoice line before saving.")

    prepared_lines: list[dict[str, Any]] = []
    calculated_subtotal = Decimal("0.00")
    calculated_tax = Decimal("0.00")
    for index, line in enumerate(lines, start=1):
        description = _clean_text(line.get("description"))
        if not description:
            raise ValueError(f"Invoice line {index}: description is required.")
        quantity = _decimal(line.get("quantity")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        unit_price = _decimal(line.get("unit_price")).quantize(CENT, rounding=ROUND_HALF_UP)
        tax_rate = _decimal(line.get("tax_rate")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        if quantity <= 0:
            raise ValueError(f"Invoice line {index}: quantity must be greater than zero.")
        if unit_price < 0:
            raise ValueError(f"Invoice line {index}: unit price cannot be negative.")
        if tax_rate < 0 or tax_rate > 100:
            raise ValueError(f"Invoice line {index}: tax rate must be between 0 and 100.")
        subtotal = (quantity * unit_price).quantize(CENT, rounding=ROUND_HALF_UP)
        tax = (subtotal * tax_rate / Decimal("100")).quantize(CENT, rounding=ROUND_HALF_UP)
        total = subtotal + tax
        calculated_subtotal += subtotal
        calculated_tax += tax
        tax_code = _clean_text(line.get("tax_code")) or None
        prepared_lines.append({
            "line_number": index,
            "sku": _clean_text(line.get("sku")) or None,
            "description": description[:255],
            "uom": (_clean_text(line.get("uom")) or "UNIT")[:20],
            "quantity": quantity,
            "unit_price": unit_price,
            "tax_code": tax_code,
            "tax_rate": tax_rate,
            "subtotal": subtotal,
            "tax": tax,
            "total": total,
        })

    calculated_subtotal = calculated_subtotal.quantize(CENT)
    calculated_tax = calculated_tax.quantize(CENT)
    calculated_total = (calculated_subtotal + calculated_tax).quantize(CENT)
    declared_subtotal_value = _money(declared_subtotal) if declared_subtotal not in (None, "") else None
    declared_tax_value = _money(declared_tax) if declared_tax not in (None, "") else None
    declared_total_value = _money(declared_total) if declared_total not in (None, "") else None
    if declared_total_value is None:
        raise ValueError("Declared invoice total is required. Confirm it from the source invoice.")

    coding_rows = non_po_codings or []
    prepared_codings: list[dict[str, Any]] = []
    if po_id is None:
        if not coding_rows:
            raise ValueError("Non-PO invoices require manual account coding before they can be saved.")
        coding_sum = Decimal("0.00")
        for index, coding in enumerate(coding_rows, start=1):
            account = _clean_text(coding.get("expense_account_code"))
            department = _clean_text(coding.get("department_code"))
            cost_center = _clean_text(coding.get("cost_center_code"))
            amount = _money(coding.get("coding_amount"))
            if not account or not department or not cost_center or amount < 0:
                raise ValueError(f"Non-PO coding row {index}: account, department, cost centre and a non-negative amount are required.")
            coding_sum += amount
            prepared_codings.append({
                "expense_account_code": account,
                "department_code": department,
                "cost_center_code": cost_center,
                "project_code": _clean_text(coding.get("project_code")) or None,
                "coding_amount": amount,
                "coding_notes": (_clean_text(coding.get("coding_notes")) or "Manual coding reviewed during invoice intake")[:500],
            })
        if abs(coding_sum - calculated_total) > CENT:
            raise ValueError(
                f"Non-PO account coding total ({coding_sum}) must equal the calculated invoice total ({calculated_total})."
            )

    relative_path, file_hash, _absolute_path = _relative_upload_path(file_bytes, filename)
    issues: list[tuple[str, str, str]] = []
    invoice_id: str | None = None
    try:
        engine = get_engine()
        with engine.begin() as conn:
            vendor = conn.execute(
                text("SELECT vendor_id, vendor_status FROM vendors WHERE vendor_id = :vendor_id"),
                {"vendor_id": vendor_id},
            ).mappings().first()
            if not vendor:
                raise ValueError(f"Vendor not found: {vendor_id}")
            if submitted_by:
                user_exists = conn.execute(text("SELECT 1 FROM users WHERE user_id = :user_id AND is_active = 1"), {"user_id": submitted_by}).first()
                if not user_exists:
                    raise ValueError(f"Submitting user is missing or inactive: {submitted_by}")
            if po_id:
                po = conn.execute(
                    text("SELECT po_id FROM purchase_orders WHERE po_id = :po_id AND vendor_id = :vendor_id"),
                    {"po_id": po_id, "vendor_id": vendor_id},
                ).first()
                if not po:
                    raise ValueError("Selected purchase order does not belong to the selected vendor.")

            duplicates_by_number = conn.execute(
                text("""SELECT invoice_id FROM invoices
                        WHERE vendor_id = :vendor_id
                          AND supplier_invoice_number = :supplier_number
                        ORDER BY received_at ASC LIMIT 1"""),
                {"vendor_id": vendor_id, "supplier_number": supplier_invoice_number},
            ).first()
            duplicates_by_hash = conn.execute(
                text("SELECT invoice_id FROM invoices WHERE source_file_hash = :file_hash ORDER BY received_at ASC LIMIT 1"),
                {"file_hash": file_hash},
            ).first()
            original_invoice_id = (duplicates_by_number[0] if duplicates_by_number else None) or (duplicates_by_hash[0] if duplicates_by_hash else None)
            if duplicates_by_number:
                issues.append(("DUPLICATE_INVOICE", "CRITICAL", f"Supplier invoice number {supplier_invoice_number} already exists for this vendor; original invoice: {duplicates_by_number[0]}"))
            elif duplicates_by_hash:
                issues.append(("DUPLICATE_DOCUMENT", "CRITICAL", f"The uploaded document is identical to existing invoice {duplicates_by_hash[0]}"))

            if declared_subtotal_value is not None and abs(declared_subtotal_value - calculated_subtotal) > CENT:
                issues.append(("SUBTOTAL_MISMATCH", "MEDIUM", f"Declared subtotal {declared_subtotal_value} differs from calculated line subtotal {calculated_subtotal}."))
            if declared_tax_value is not None and abs(declared_tax_value - calculated_tax) > CENT:
                issues.append(("TAX_MISMATCH", "MEDIUM", f"Declared tax {declared_tax_value} differs from calculated line tax {calculated_tax}."))
            if abs(declared_total_value - calculated_total) > CENT:
                issues.append(("TOTAL_MISMATCH", "MEDIUM", f"Declared total {declared_total_value} differs from calculated line total {calculated_total}."))
            if vendor["vendor_status"] in ("BLOCKED", "CLOSED", "INACTIVE"):
                issues.append(("VENDOR_ERROR", "HIGH", f"Invoice submitted by vendor with status {vendor['vendor_status']}. Review vendor status before processing."))

            invoice_year = invoice_date.year
            invoice_id = _next_invoice_id(conn, vendor_id, invoice_year)
            invoice_status = "PENDING_EXCEPTION" if issues else "RECEIVED"
            match_status = "PENDING" if po_id else "NOT_APPLICABLE"
            extraction_status = "SUCCEEDED" if extraction_succeeded and raw_text.strip() else "MANUAL_REVIEW"

            conn.execute(text("""INSERT INTO invoices (
                invoice_id, vendor_id, po_id, original_invoice_id, supplier_invoice_number,
                invoice_date, due_date, received_at, currency_code,
                declared_subtotal, declared_tax, declared_total,
                calculated_subtotal, calculated_tax, calculated_total,
                invoice_status, match_status, extraction_status, source_channel,
                source_file_path, source_file_hash, submitted_by, payment_ready
            ) VALUES (
                :invoice_id, :vendor_id, :po_id, :original_invoice_id, :supplier_invoice_number,
                :invoice_date, :due_date, CURRENT_TIMESTAMP, :currency_code,
                :declared_subtotal, :declared_tax, :declared_total,
                :calculated_subtotal, :calculated_tax, :calculated_total,
                :invoice_status, :match_status, :extraction_status, 'PORTAL',
                :source_file_path, :source_file_hash, :submitted_by, 0
            )"""), {
                "invoice_id": invoice_id,
                "vendor_id": vendor_id,
                "po_id": po_id,
                "original_invoice_id": original_invoice_id,
                "supplier_invoice_number": supplier_invoice_number,
                "invoice_date": invoice_date,
                "due_date": due_date,
                "currency_code": currency_code,
                "declared_subtotal": declared_subtotal_value,
                "declared_tax": declared_tax_value,
                "declared_total": declared_total_value,
                "calculated_subtotal": calculated_subtotal,
                "calculated_tax": calculated_tax,
                "calculated_total": calculated_total,
                "invoice_status": invoice_status,
                "match_status": match_status,
                "extraction_status": extraction_status,
                "source_file_path": relative_path,
                "source_file_hash": file_hash,
                "submitted_by": submitted_by,
            })

            for line in prepared_lines:
                line_id = f"INVL-{invoice_id}-{line['line_number']:02d}"
                conn.execute(text("""INSERT INTO invoice_lines (
                    invoice_line_id, invoice_id, po_id, line_number, sku, description, uom,
                    quantity, unit_price, tax_code, tax_rate,
                    declared_line_subtotal, declared_tax_amount, declared_line_total
                ) VALUES (
                    :invoice_line_id, :invoice_id, :po_id, :line_number, :sku, :description, :uom,
                    :quantity, :unit_price, :tax_code, :tax_rate,
                    :subtotal, :tax, :total
                )"""), {
                    "invoice_line_id": line_id,
                    "invoice_id": invoice_id,
                    "po_id": po_id,
                    **line,
                })

            for coding in prepared_codings:
                conn.execute(text("""INSERT INTO invoice_account_codings (
                    coding_id, invoice_id, invoice_line_id, expense_account_code,
                    department_code, cost_center_code, project_code, coding_amount,
                    currency_code, coded_by, coding_status, coding_notes
                ) VALUES (
                    :coding_id, :invoice_id, NULL, :expense_account_code,
                    :department_code, :cost_center_code, :project_code, :coding_amount,
                    :currency_code, :coded_by, 'SUBMITTED', :coding_notes
                )"""), {
                    "coding_id": f"COD-{uuid.uuid4().hex[:20].upper()}",
                    "invoice_id": invoice_id,
                    **coding,
                    "currency_code": currency_code,
                    "coded_by": submitted_by,
                })

            for issue_type, severity, description in issues:
                conn.execute(text("""INSERT INTO invoice_exceptions (
                    exception_id, invoice_id, issue_type, severity, exception_status,
                    is_blocking, description, assigned_to
                ) VALUES (:exception_id, :invoice_id, :issue_type, :severity,
                          'OPEN', 1, :description, NULL)"""), {
                    "exception_id": f"EXC-{uuid.uuid4().hex[:24].upper()}",
                    "invoice_id": invoice_id,
                    "issue_type": issue_type,
                    "severity": severity,
                    "description": description,
                })

            conn.execute(text("""INSERT INTO invoice_events (
                event_id, invoice_id, event_type, previous_status, new_status,
                actor_id, event_reason, event_metadata
            ) VALUES (
                :event_id, :invoice_id, 'DOCUMENT_UPLOADED', NULL, :new_status,
                :actor_id, :event_reason, :event_metadata
            )"""), {
                "event_id": f"IEVT-{uuid.uuid4().hex[:24].upper()}",
                "invoice_id": invoice_id,
                "new_status": invoice_status,
                "actor_id": submitted_by,
                "event_reason": "Invoice document uploaded, OCR reviewed and saved.",
                "event_metadata": __import__("json").dumps({
                    "original_filename": Path(filename).name,
                    "source_file_path": relative_path,
                    "source_file_hash": file_hash,
                    "line_count": len(prepared_lines),
                }),
            })
    except Exception:
        # Keep the uploaded source file for auditability; it is not referenced if the DB transaction fails.
        raise

    return {
        "invoice_id": invoice_id,
        "calculated_subtotal": str(calculated_subtotal),
        "calculated_tax": str(calculated_tax),
        "calculated_total": str(calculated_total),
        "invoice_status": invoice_status,
        "match_status": match_status,
        "payment_ready": False,
        "original_invoice_id": original_invoice_id,
        "exceptions_created": [item[0] for item in issues],
        "source_file_path": relative_path,
    }
