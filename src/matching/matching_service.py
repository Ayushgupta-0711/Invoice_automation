"""PO/receipt/invoice matching and inspection services.

The automatic matcher reserves quantities already allocated to other invoices,
uses only posted receipts, and never changes approval/payment state.
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import text

from database.connection import get_engine

QTY_EPSILON = Decimal("0.0001")
MONEY_EPSILON = Decimal("0.01")
DEFAULT_PRICE_TOLERANCE = Decimal("0.01")  # 1% relative variance
DEFAULT_TAX_TOLERANCE_PP = Decimal("0.01")  # percentage points


def normalise_description(value: str | None) -> str:
    """Normalise a description for conservative PO-line matching."""
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def description_similarity(left: str | None, right: str | None) -> float:
    """Return a 0..1 description similarity score for deterministic matching."""
    a = normalise_description(left)
    b = normalise_description(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _choose_po_line(invoice_line: dict[str, Any], po_lines: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    """Choose a PO line by exact SKU, then exact/strong description similarity."""
    sku = (invoice_line.get("sku") or "").strip().casefold()
    if sku:
        sku_matches = [row for row in po_lines if (row.get("sku") or "").strip().casefold() == sku]
        if len(sku_matches) == 1:
            return sku_matches[0], 1.0
        if len(sku_matches) > 1:
            return None, 0.0

    scored = [(description_similarity(invoice_line.get("description"), row.get("description")), row) for row in po_lines]
    if not scored:
        return None, 0.0
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_row = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    # Require a strong score and a clear winner to avoid silently mapping a line incorrectly.
    if best_score >= 0.86 and (best_score - second_score >= 0.04 or best_score == 1.0):
        return best_row, best_score
    return None, best_score


def inspect_invoice_matches(invoice_id: str) -> dict[str, Any]:
    """Return line-level match allocations and receipt support for an invoice."""
    sql = text("""SELECT
        il.invoice_line_id,
        il.description,
        il.quantity AS invoice_quantity,
        COALESCE(SUM(m.matched_quantity), 0) AS allocated_match_quantity,
        COALESCE(SUM(grl.accepted_quantity), 0) AS joined_accepted_quantity
    FROM invoice_lines il
    LEFT JOIN invoice_po_line_matches m
        ON m.invoice_line_id = il.invoice_line_id
       AND m.invoice_id = il.invoice_id
    LEFT JOIN goods_receipt_lines grl
        ON grl.receipt_line_id = m.receipt_line_id
    WHERE il.invoice_id = :invoice_id
    GROUP BY il.invoice_line_id, il.description, il.quantity, il.line_number
    ORDER BY il.line_number""")
    with get_engine().connect() as conn:
        rows = conn.execute(sql, {"invoice_id": invoice_id}).mappings().all()
    if not rows:
        raise ValueError(f"No invoice lines found for {invoice_id}")

    results = []
    for row in rows:
        invoice_qty = _decimal(row["invoice_quantity"])
        matched_qty = _decimal(row["allocated_match_quantity"])
        results.append({
            "invoice_line_id": row["invoice_line_id"],
            "description": row["description"],
            "invoice_quantity": float(invoice_qty),
            "matched_quantity": float(matched_qty),
            "match_allocation_complete": abs(invoice_qty - matched_qty) <= QTY_EPSILON,
        })
    return {
        "invoice_id": invoice_id,
        "lines": results,
        "all_allocations_complete": all(item["match_allocation_complete"] for item in results),
    }


def run_three_way_match(
    invoice_id: str,
    *,
    price_tolerance: Decimal = DEFAULT_PRICE_TOLERANCE,
    tax_tolerance_pp: Decimal = DEFAULT_TAX_TOLERANCE_PP,
) -> dict[str, Any]:
    """Run transactional PO/GRN/invoice matching and persist allocations/exceptions.

    Non-PO invoices are marked NOT_APPLICABLE. Duplicate invoices are not
    allocated against receipts, preventing a duplicate from consuming capacity.
    """
    issues: list[dict[str, str]] = []
    line_results: list[dict[str, Any]] = []
    allocations: list[dict[str, Any]] = []

    with get_engine().begin() as conn:
        header = conn.execute(text("""SELECT
                i.invoice_id, i.vendor_id, i.po_id, i.original_invoice_id,
                i.supplier_invoice_number, i.declared_total, i.calculated_total,
                i.invoice_status, v.vendor_status, po.po_status, po.po_date
            FROM invoices i
            JOIN vendors v ON v.vendor_id = i.vendor_id
            LEFT JOIN purchase_orders po ON po.po_id = i.po_id
            WHERE i.invoice_id = :invoice_id
            FOR UPDATE"""), {"invoice_id": invoice_id}).mappings().first()
        if not header:
            raise ValueError(f"Invoice not found: {invoice_id}")

        if header["po_id"] is None:
            conn.execute(text("UPDATE invoices SET match_status = 'NOT_APPLICABLE' WHERE invoice_id = :invoice_id"), {"invoice_id": invoice_id})
            return {
                "invoice_id": invoice_id,
                "match_status": "NOT_APPLICABLE",
                "message": "This is a non-PO invoice. Complete account coding and approval instead of three-way matching.",
                "allocations_created": 0,
                "exceptions_created": 0,
                "issues": [],
                "lines": [],
            }

        po_id = str(header["po_id"])
        if header["vendor_status"] != "ACTIVE":
            issues.append({"issue_type": "VENDOR_ERROR", "severity": "HIGH", "description": f"Vendor status is {header['vendor_status']}; PO invoice processing requires an active vendor."})
        if header["po_status"] in ("CANCELLED", "DRAFT"):
            issues.append({"issue_type": "PO_NOT_ACTIVE", "severity": "HIGH", "description": f"Purchase order status is {header['po_status']}."})
        # A duplicate should remain in the audit trail but must not reserve receipts again.
        duplicate = bool(header["original_invoice_id"])
        duplicate_of = header["original_invoice_id"]
        number = (header["supplier_invoice_number"] or "").strip()
        if number:
            prior = conn.execute(text("""SELECT invoice_id FROM invoices
                WHERE vendor_id = :vendor_id
                  AND supplier_invoice_number = :number
                  AND invoice_id <> :invoice_id
                ORDER BY received_at ASC LIMIT 1"""), {
                    "vendor_id": header["vendor_id"], "number": number, "invoice_id": invoice_id,
                }).scalar()
            if prior:
                duplicate = True
                duplicate_of = duplicate_of or prior
        if duplicate:
            issues.append({
                "issue_type": "DUPLICATE_INVOICE", "severity": "CRITICAL",
                "description": f"Invoice appears to duplicate {duplicate_of or 'an earlier invoice'}; matching allocations were not created.",
            })

        declared_total = _decimal(header["declared_total"])
        calculated_total = _decimal(header["calculated_total"])
        if header["declared_total"] is not None and abs(declared_total - calculated_total) > MONEY_EPSILON:
            issues.append({
                "issue_type": "TOTAL_MISMATCH", "severity": "MEDIUM",
                "description": f"Declared total {declared_total:.2f} differs from calculated total {calculated_total:.2f} by {declared_total - calculated_total:.2f}.",
            })

        invoice_lines = conn.execute(text("""SELECT invoice_line_id, line_number, sku,
                description, quantity, unit_price, tax_rate
            FROM invoice_lines WHERE invoice_id = :invoice_id ORDER BY line_number"""),
            {"invoice_id": invoice_id}).mappings().all()
        if not invoice_lines:
            raise ValueError(f"No invoice lines found for {invoice_id}")

        # Clear only previous allocations and exceptions generated by this matcher; do not
        # delete manually recorded exceptions or historical invoice/approval/payment events.
        conn.execute(text("DELETE FROM invoice_po_line_matches WHERE invoice_id = :invoice_id"), {"invoice_id": invoice_id})
        conn.execute(text("""DELETE FROM invoice_exceptions
            WHERE invoice_id = :invoice_id AND description LIKE '[AUTO_MATCH]%'"""), {"invoice_id": invoice_id})

        po_lines = [dict(row) for row in conn.execute(text("""SELECT po_line_id, sku,
                description, ordered_quantity, unit_price, tax_rate
            FROM purchase_order_lines WHERE po_id = :po_id ORDER BY line_number"""),
            {"po_id": po_id}).mappings().all()]
        receipt_rows = conn.execute(text("""SELECT grl.receipt_line_id, grl.po_line_id,
                grl.received_quantity, grl.accepted_quantity, gr.receipt_date, gr.receipt_id
            FROM goods_receipt_lines grl
            JOIN goods_receipts gr ON gr.receipt_id = grl.receipt_id AND gr.po_id = grl.po_id
            WHERE grl.po_id = :po_id AND gr.receipt_status = 'POSTED'
            ORDER BY gr.receipt_date, gr.receipt_id, grl.line_number"""),
            {"po_id": po_id}).mappings().all()
        receipts_by_line: dict[str, list[dict[str, Any]]] = {}
        for row in receipt_rows:
            receipts_by_line.setdefault(str(row["po_line_id"]), []).append(dict(row))

        prior_po_allocations = {
            str(row["po_line_id"]): _decimal(row["allocated_qty"])
            for row in conn.execute(text("""SELECT po_line_id, SUM(matched_quantity) AS allocated_qty
                FROM invoice_po_line_matches
                WHERE po_id = :po_id AND invoice_id <> :invoice_id
                GROUP BY po_line_id"""), {"po_id": po_id, "invoice_id": invoice_id}).mappings().all()
        }
        prior_receipt_allocations = {
            str(row["receipt_line_id"]): _decimal(row["allocated_qty"])
            for row in conn.execute(text("""SELECT receipt_line_id, SUM(matched_quantity) AS allocated_qty
                FROM invoice_po_line_matches
                WHERE po_id = :po_id AND invoice_id <> :invoice_id AND receipt_line_id IS NOT NULL
                GROUP BY receipt_line_id"""), {"po_id": po_id, "invoice_id": invoice_id}).mappings().all()
        }
        in_run_po_allocations: dict[str, Decimal] = {}
        in_run_receipt_allocations: dict[str, Decimal] = {}

        if not duplicate:
            for invoice_line in invoice_lines:
                line = dict(invoice_line)
                po_line, confidence = _choose_po_line(line, po_lines)
                if po_line is None:
                    issues.append({
                        "issue_type": "PO_LINE_NOT_FOUND", "severity": "HIGH",
                        "description": f"Invoice line {line['line_number']} ({line['description']}) could not be confidently matched to a PO line (best description score {confidence:.2f}).",
                    })
                    line_results.append({"invoice_line_id": line["invoice_line_id"], "description": line["description"], "status": "UNMATCHED", "matched_quantity": 0.0, "invoice_quantity": float(_decimal(line["quantity"]))})
                    continue

                po_line_id = str(po_line["po_line_id"])
                invoice_qty = _decimal(line["quantity"])
                ordered_qty = _decimal(po_line["ordered_quantity"])
                prior_billed = prior_po_allocations.get(po_line_id, Decimal("0")) + in_run_po_allocations.get(po_line_id, Decimal("0"))
                po_remaining = max(ordered_qty - prior_billed, Decimal("0"))

                po_price = _decimal(po_line["unit_price"])
                invoice_price = _decimal(line["unit_price"])
                price_variance = (abs(invoice_price - po_price) / abs(po_price)) if po_price != 0 else (Decimal("0") if invoice_price == 0 else Decimal("1"))
                line_has_variance = False
                if price_variance > price_tolerance:
                    line_has_variance = True
                    issues.append({"issue_type": "PRICE_VARIANCE", "severity": "MEDIUM", "description": f"Invoice line {line['line_number']} unit price {invoice_price:.2f} differs from PO unit price {po_price:.2f} by {price_variance * 100:.2f}%."})

                po_tax = _decimal(po_line["tax_rate"])
                invoice_tax = _decimal(line["tax_rate"])
                if abs(invoice_tax - po_tax) > tax_tolerance_pp:
                    line_has_variance = True
                    issues.append({"issue_type": "TAX_MISMATCH", "severity": "MEDIUM", "description": f"Invoice line {line['line_number']} tax rate {invoice_tax:.4f}% differs from PO tax rate {po_tax:.4f}%."})

                line_receipts = receipts_by_line.get(po_line_id, [])
                receipt_available: list[tuple[dict[str, Any], Decimal]] = []
                for receipt in line_receipts:
                    receipt_id = str(receipt["receipt_line_id"])
                    available = max(
                        _decimal(receipt["accepted_quantity"])
                        - prior_receipt_allocations.get(receipt_id, Decimal("0"))
                        - in_run_receipt_allocations.get(receipt_id, Decimal("0")),
                        Decimal("0"),
                    )
                    if available > 0:
                        receipt_available.append((receipt, available))
                accepted_available = sum((amount for _, amount in receipt_available), Decimal("0"))
                max_allocatable = min(po_remaining, accepted_available)
                to_allocate = min(invoice_qty, max_allocatable)
                remaining = to_allocate
                line_allocations: list[dict[str, Any]] = []
                for receipt, available in receipt_available:
                    if remaining <= QTY_EPSILON:
                        break
                    qty = min(remaining, available)
                    if qty <= 0:
                        continue
                    receipt_line_id = str(receipt["receipt_line_id"])
                    line_allocations.append({
                        "match_id": f"MATCH-{uuid.uuid4().hex[:24]}",
                        "invoice_id": invoice_id,
                        "po_id": po_id,
                        "invoice_line_id": str(line["invoice_line_id"]),
                        "po_line_id": po_line_id,
                        "receipt_line_id": receipt_line_id,
                        "matched_quantity": qty,
                    })
                    in_run_receipt_allocations[receipt_line_id] = in_run_receipt_allocations.get(receipt_line_id, Decimal("0")) + qty
                    remaining -= qty
                allocated_qty = to_allocate - remaining
                in_run_po_allocations[po_line_id] = in_run_po_allocations.get(po_line_id, Decimal("0")) + allocated_qty

                if allocated_qty + QTY_EPSILON < invoice_qty:
                    line_has_variance = True
                    if po_remaining + QTY_EPSILON < invoice_qty:
                        reason = f"invoice quantity {invoice_qty} exceeds remaining uninvoiced PO quantity {po_remaining}"
                    else:
                        reason = f"invoice quantity {invoice_qty} exceeds available accepted receipt quantity {accepted_available}"
                    issues.append({"issue_type": "QUANTITY_MISMATCH", "severity": "HIGH", "description": f"Invoice line {line['line_number']} ({line['description']}): {reason}. Only {allocated_qty} unit(s) could be allocated."})

                result_code = "VARIANCE" if line_has_variance else "MATCHED"
                for allocation in line_allocations:
                    allocation["match_result"] = result_code
                    allocations.append(allocation)
                line_results.append({
                    "invoice_line_id": line["invoice_line_id"],
                    "description": line["description"],
                    "po_line_id": po_line_id,
                    "description_match_confidence": round(confidence, 3),
                    "invoice_quantity": float(invoice_qty),
                    "remaining_po_quantity_before_match": float(po_remaining),
                    "available_accepted_receipt_quantity": float(accepted_available),
                    "matched_quantity": float(allocated_qty),
                    "status": result_code if allocated_qty + QTY_EPSILON >= invoice_qty else "VARIANCE",
                })

        # A non-duplicate line can still be fully quantity-matched while the header has an issue.
        for allocation in allocations:
            conn.execute(text("""INSERT INTO invoice_po_line_matches (
                match_id, invoice_id, po_id, invoice_line_id, po_line_id,
                receipt_line_id, matched_quantity, match_result
            ) VALUES (
                :match_id, :invoice_id, :po_id, :invoice_line_id, :po_line_id,
                :receipt_line_id, :matched_quantity, :match_result
            )"""), allocation)

        # Convert issues to durable, blocking exception rows. Prefix marks rows owned by this matcher,
        # allowing a safe rerun without deleting manually created exceptions.
        for issue in issues:
            exception_id = f"EXC-{uuid.uuid4().hex[:24]}"
            conn.execute(text("""INSERT INTO invoice_exceptions (
                exception_id, invoice_id, issue_type, severity, exception_status,
                is_blocking, description, detected_at
            ) VALUES (
                :exception_id, :invoice_id, :issue_type, :severity, 'OPEN',
                1, :description, CURRENT_TIMESTAMP
            )"""), {
                "exception_id": exception_id,
                "invoice_id": invoice_id,
                "issue_type": issue["issue_type"],
                "severity": issue["severity"],
                "description": "[AUTO_MATCH] " + issue["description"],
            })

        match_status = "VARIANCE" if issues else "MATCHED"
        current_status = str(header["invoice_status"])
        target_invoice_status = "PENDING_EXCEPTION" if issues else current_status
        if not issues and current_status == "PENDING_EXCEPTION":
            remaining_blockers = conn.execute(text("""SELECT COUNT(*) FROM invoice_exceptions
                WHERE invoice_id = :invoice_id AND is_blocking = 1
                  AND exception_status IN ('OPEN', 'IN_REVIEW')"""), {"invoice_id": invoice_id}).scalar_one()
            if int(remaining_blockers) == 0:
                target_invoice_status = "PROCESSING"

        conn.execute(text("""UPDATE invoices
            SET match_status = :match_status,
                invoice_status = :invoice_status,
                payment_ready = 0
            WHERE invoice_id = :invoice_id"""), {
                "match_status": match_status,
                "invoice_status": target_invoice_status,
                "invoice_id": invoice_id,
            })

        conn.execute(text("""INSERT INTO invoice_events (
            event_id, invoice_id, event_type, previous_status, new_status,
            actor_id, event_reason, event_metadata, occurred_at
        ) VALUES (
            :event_id, :invoice_id, 'MATCHING_COMPLETED', :previous_status,
            :new_status, NULL, :event_reason, :event_metadata, CURRENT_TIMESTAMP
        )"""), {
            "event_id": f"IEVT-{uuid.uuid4().hex[:24]}",
            "invoice_id": invoice_id,
            "previous_status": current_status,
            "new_status": target_invoice_status,
            "event_reason": f"Automatic matching completed with status {match_status}.",
            "event_metadata": None,
        })

    return {
        "invoice_id": invoice_id,
        "match_status": match_status,
        "invoice_status": target_invoice_status,
        "allocations_created": len(allocations),
        "exceptions_created": len(issues),
        "payment_ready": False,
        "issues": issues,
        "lines": line_results,
        "note": "Matching does not approve invoices or authorize payment. Approval and payment controls run separately.",
    }
