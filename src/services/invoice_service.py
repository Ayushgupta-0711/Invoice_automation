"""Invoice validation, duplicate detection and detail retrieval."""
from __future__ import annotations

from decimal import Decimal
from sqlalchemy import text
from database.connection import get_engine

MONEY_TOLERANCE = Decimal("0.01")


def get_invoice(invoice_id: str) -> dict | None:
    sql = text("""SELECT i.*, v.vendor_name, v.vendor_status
                 FROM invoices i JOIN vendors v ON v.vendor_id = i.vendor_id
                 WHERE i.invoice_id = :invoice_id""")
    with get_engine().connect() as conn:
        row = conn.execute(sql, {"invoice_id": invoice_id}).mappings().first()
        return dict(row) if row else None


def validate_invoice(invoice_id: str) -> dict:
    """Recalculate totals from invoice lines and add blocking exceptions where needed.

    This is a controlled first-pass validator. It does not approve invoices or
    mark them payment-ready; those actions require separate workflow checks.
    """
    engine = get_engine()
    with engine.begin() as conn:
        invoice = conn.execute(
            text("""SELECT invoice_id, vendor_id, supplier_invoice_number,
                             declared_subtotal, declared_tax, declared_total,
                             invoice_status
                      FROM invoices WHERE invoice_id = :invoice_id FOR UPDATE"""),
            {"invoice_id": invoice_id},
        ).mappings().first()
        if not invoice:
            raise ValueError(f"Invoice not found: {invoice_id}")

        totals = conn.execute(
            text("""SELECT
                    COALESCE(SUM(quantity * unit_price), 0) AS subtotal,
                    COALESCE(SUM(quantity * unit_price * tax_rate / 100), 0) AS tax
                FROM invoice_lines WHERE invoice_id = :invoice_id"""),
            {"invoice_id": invoice_id},
        ).mappings().one()
        subtotal = Decimal(str(totals["subtotal"])).quantize(Decimal("0.01"))
        tax = Decimal(str(totals["tax"])).quantize(Decimal("0.01"))
        total = (subtotal + tax).quantize(Decimal("0.01"))

        conn.execute(
            text("""UPDATE invoices
                    SET calculated_subtotal = :subtotal,
                        calculated_tax = :tax,
                        calculated_total = :total
                    WHERE invoice_id = :invoice_id"""),
            {"subtotal": subtotal, "tax": tax, "total": total, "invoice_id": invoice_id},
        )

        issues: list[tuple[str, str, str]] = []
        duplicate = conn.execute(
            text("""SELECT invoice_id FROM invoices
                    WHERE vendor_id = :vendor_id
                      AND supplier_invoice_number = :supplier_number
                      AND invoice_id <> :invoice_id
                    ORDER BY received_at ASC LIMIT 1"""),
            {
                "vendor_id": invoice["vendor_id"],
                "supplier_number": invoice["supplier_invoice_number"],
                "invoice_id": invoice_id,
            },
        ).first()
        if duplicate and invoice["supplier_invoice_number"]:
            issues.append(("DUPLICATE_INVOICE", "CRITICAL",
                           f"Possible duplicate of invoice {duplicate[0]} for the same vendor and supplier invoice number."))

        declared_total = invoice["declared_total"]
        if declared_total is not None and abs(Decimal(str(declared_total)) - total) > MONEY_TOLERANCE:
            difference = (Decimal(str(declared_total)) - total).quantize(Decimal("0.01"))
            issues.append(("TOTAL_MISMATCH", "MEDIUM",
                           f"Declared total differs from calculated line total by {difference} {invoice_id} currency."))

        for issue_type, severity, description in issues:
            exists = conn.execute(
                text("""SELECT 1 FROM invoice_exceptions
                        WHERE invoice_id = :invoice_id AND issue_type = :issue_type
                          AND exception_status IN ('OPEN','IN_REVIEW') LIMIT 1"""),
                {"invoice_id": invoice_id, "issue_type": issue_type},
            ).first()
            if not exists:
                conn.execute(
                    text("""INSERT INTO invoice_exceptions
                        (exception_id, invoice_id, issue_type, severity,
                         exception_status, is_blocking, description)
                        VALUES (:id, :invoice_id, :issue_type, :severity,
                                'OPEN', 1, :description)"""),
                    {
                        "id": f"EXC-{invoice_id[-12:]}-{issue_type[:8]}",
                        "invoice_id": invoice_id,
                        "issue_type": issue_type,
                        "severity": severity,
                        "description": description,
                    },
                )

        open_blockers = conn.execute(
            text("""SELECT COUNT(*) FROM invoice_exceptions
                    WHERE invoice_id = :invoice_id
                      AND is_blocking = 1
                      AND exception_status IN ('OPEN','IN_REVIEW')"""),
            {"invoice_id": invoice_id},
        ).scalar_one()

        if open_blockers:
            conn.execute(
                text("""UPDATE invoices SET invoice_status = 'PENDING_EXCEPTION',
                        payment_ready = 0 WHERE invoice_id = :invoice_id"""),
                {"invoice_id": invoice_id},
            )
        else:
            # Validation alone never approves an invoice or enables payment.
            conn.execute(
                text("UPDATE invoices SET payment_ready = 0 WHERE invoice_id = :invoice_id"),
                {"invoice_id": invoice_id},
            )

        return {
            "invoice_id": invoice_id,
            "calculated_subtotal": str(subtotal),
            "calculated_tax": str(tax),
            "calculated_total": str(total),
            "issues_found": len(issues),
            "issues": [issue[0] for issue in issues],
            "open_blocking_exceptions": int(open_blockers),
            "payment_ready": False,
        }
