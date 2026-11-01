"""Streamlit invoice upload, OCR review and persistence workflow."""
from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from src.ocr.engine import extract_text_from_bytes
from src.ocr.parser import parse_invoice_text
from src.services.invoice_intake_service import (
    MAX_UPLOAD_BYTES,
    find_possible_duplicates,
    list_tax_codes,
    list_users,
    list_vendor_purchase_orders,
    list_vendors,
    save_reviewed_invoice,
)


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    cleaned = str(value).replace(",", "").replace("₹", "").replace("INR", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _line_frame(parsed_lines: list[dict[str, Any]], default_tax_code: str = "") -> pd.DataFrame:
    if parsed_lines:
        rows = parsed_lines
    else:
        rows = [{
            "description": "",
            "sku": "",
            "uom": "UNIT",
            "quantity": 1.0,
            "unit_price": 0.0,
            "tax_code": default_tax_code,
            "tax_rate": 0.0,
        }]
    return pd.DataFrame(rows, columns=[
        "description", "sku", "uom", "quantity", "unit_price", "tax_code", "tax_rate"
    ])


def _ui_decimal(value: Any) -> Decimal:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return Decimal("0")
    return Decimal(str(value))


def _preview_totals(frame: pd.DataFrame) -> tuple[Decimal, Decimal, Decimal]:
    subtotal = Decimal("0.00")
    tax = Decimal("0.00")
    for row in frame.to_dict(orient="records"):
        if not str(row.get("description") or "").strip():
            continue
        qty = _ui_decimal(row.get("quantity"))
        price = _ui_decimal(row.get("unit_price"))
        rate = _ui_decimal(row.get("tax_rate"))
        line_subtotal = (qty * price).quantize(Decimal("0.01"))
        subtotal += line_subtotal
        tax += (line_subtotal * rate / Decimal("100")).quantize(Decimal("0.01"))
    subtotal = subtotal.quantize(Decimal("0.01"))
    tax = tax.quantize(Decimal("0.01"))
    return subtotal, tax, (subtotal + tax).quantize(Decimal("0.01"))


def _normalise_po(value: str | None) -> str | None:
    return value.replace(" ", "-").upper() if value else None


def render_invoice_intake() -> None:
    st.subheader("Invoice Upload & OCR Intake")
    st.caption("Upload a PDF or image, extract likely fields, review every value, then save the verified invoice to MySQL.")
    st.info("OCR results are suggestions, not accounting decisions. Review vendor, invoice number, dates, quantities, prices and tax before saving.")

    uploaded = st.file_uploader(
        "Choose an invoice document",
        type=["pdf", "png", "jpg", "jpeg", "tif", "tiff", "bmp"],
        key="invoice_intake_uploader",
    )
    if uploaded is None:
        st.markdown("Supported formats: PDF, PNG, JPG/JPEG, TIFF and BMP. Maximum size: 15 MB.")
        return

    file_bytes = uploaded.getvalue()
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        st.error("The uploaded file exceeds the 15 MB limit. Please compress or split the document.")
        return
    current_digest = hashlib.sha256(file_bytes).hexdigest()
    if st.session_state.get("intake_upload_digest") != current_digest:
        for key in list(st.session_state.keys()):
            if key.startswith("intake_") and key not in {"intake_upload_digest"}:
                del st.session_state[key]
        st.session_state["intake_upload_digest"] = current_digest
        st.session_state["intake_filename"] = Path(uploaded.name).name

    st.caption(f"Selected: {Path(uploaded.name).name} · {len(file_bytes) / 1024:.1f} KB")
    if Path(uploaded.name).suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        st.image(file_bytes, caption="Uploaded invoice image", use_container_width=True)

    if st.button("Extract text and suggest fields", type="primary", key="intake_extract_button"):
        try:
            with st.spinner("Reading document and running OCR where needed..."):
                extracted = extract_text_from_bytes(file_bytes, uploaded.name)
                parsed = parse_invoice_text(extracted)
            st.session_state["intake_raw_text"] = extracted
            st.session_state["intake_parsed"] = parsed
            st.session_state["intake_extracted"] = True
            st.session_state["intake_invoice_number"] = parsed.get("supplier_invoice_number") or ""
            st.session_state["intake_invoice_date"] = parsed.get("invoice_date") or date.today()
            st.session_state["intake_due_date"] = parsed.get("due_date") or (date.today() + timedelta(days=30))
            st.session_state["intake_declared_subtotal"] = str(parsed.get("declared_subtotal") or "")
            st.session_state["intake_declared_tax"] = str(parsed.get("declared_tax") or "")
            st.session_state["intake_declared_total"] = str(parsed.get("declared_total") or "")
            st.session_state["intake_currency"] = "INR"
            st.session_state["intake_lines_frame"] = _line_frame(parsed.get("line_items", []))
            st.session_state["intake_saved_invoice_id"] = None
            st.success(f"Document read. Extracted {len(extracted)} characters. Review the suggestions below.")
            st.rerun()
        except Exception as exc:
            st.error(f"Document extraction failed: {exc}")
            return

    if not st.session_state.get("intake_extracted"):
        st.warning("Select **Extract text and suggest fields** to begin the review step.")
        return

    parsed = st.session_state.get("intake_parsed", {})
    raw_text = st.session_state.get("intake_raw_text", "")
    with st.expander("View extracted document text", expanded=False):
        st.text_area("OCR / PDF text", value=raw_text, height=220, disabled=True, key="intake_raw_text_preview")

    try:
        vendors = list_vendors()
        tax_codes = list_tax_codes()
        users = list_users()
    except Exception as exc:
        st.error(f"Could not load vendor, tax-code or user master data: {exc}")
        return
    if not vendors:
        st.error("No vendors are configured in the vendors table.")
        return

    vendor_by_id = {v["vendor_id"]: v for v in vendors}
    vendor_ids = list(vendor_by_id)
    if "intake_vendor_id" not in st.session_state:
        text_lower = raw_text.lower()
        match = next((v["vendor_id"] for v in vendors if v["vendor_name"].lower() in text_lower), vendor_ids[0])
        st.session_state["intake_vendor_id"] = match

    st.markdown("### Review invoice header")
    vendor_id = st.selectbox(
        "Vendor",
        options=vendor_ids,
        format_func=lambda value: f"{vendor_by_id[value]['vendor_name']} ({vendor_by_id[value]['vendor_code']}) — {vendor_by_id[value]['vendor_status']}",
        key="intake_vendor_id",
    )
    selected_vendor = vendor_by_id[vendor_id]
    if selected_vendor["vendor_status"] != "ACTIVE":
        st.warning(f"Vendor status is {selected_vendor['vendor_status']}. The invoice can be recorded, but a blocking vendor exception will be created.")

    col1, col2 = st.columns(2)
    with col1:
        supplier_number = st.text_input("Supplier invoice number *", key="intake_invoice_number")
        invoice_date = st.date_input("Invoice date *", key="intake_invoice_date")
        currency = st.selectbox("Currency", ["INR", "USD", "EUR", "GBP"], key="intake_currency")
    with col2:
        use_due_date = st.checkbox("Invoice has a due date", value=bool(parsed.get("due_date")), key="intake_has_due_date")
        due_date = st.date_input("Due date", key="intake_due_date", disabled=not use_due_date) if use_due_date else None
        submitter_ids = [None] + [u["user_id"] for u in users]
        user_by_id = {u["user_id"]: u for u in users}
        submitted_by = st.selectbox(
            "Submitted / recorded by",
            options=submitter_ids,
            format_func=lambda value: "Not specified" if value is None else f"{user_by_id[value]['full_name']} ({user_by_id[value]['role']})",
            key="intake_submitted_by",
        )

    po_records = list_vendor_purchase_orders(vendor_id)
    po_ids = [None] + [row["po_id"] for row in po_records]
    po_labels = {row["po_id"]: f"{row['po_id']} · {row['po_status']} · {row['po_date']}" for row in po_records}
    suggested_po = _normalise_po(parsed.get("po_reference"))
    if "intake_po_id" not in st.session_state:
        st.session_state["intake_po_id"] = next((po for po in po_ids if po and po.upper() == suggested_po), None)
    elif st.session_state.get("intake_po_id") not in po_ids:
        st.session_state["intake_po_id"] = None
    po_id = st.selectbox(
        "Purchase order (select Non-PO when no PO applies)",
        options=po_ids,
        format_func=lambda value: "Non-PO invoice" if value is None else po_labels[value],
        key="intake_po_id",
    )

    if supplier_number.strip():
        try:
            duplicates = find_possible_duplicates(vendor_id, supplier_number.strip(), current_digest)
            if duplicates:
                st.warning("Possible duplicate detected. Saving is allowed for audit purposes; the new invoice will reference the earlier record and receive a blocking exception.")
                st.dataframe(pd.DataFrame(duplicates), use_container_width=True, hide_index=True)
        except Exception as exc:
            st.warning(f"Could not check duplicates before save: {exc}")

    st.markdown("### Review declared amounts")
    amount_cols = st.columns(3)
    with amount_cols[0]:
        declared_subtotal_text = st.text_input("Declared subtotal", key="intake_declared_subtotal")
    with amount_cols[1]:
        declared_tax_text = st.text_input("Declared tax", key="intake_declared_tax")
    with amount_cols[2]:
        declared_total_text = st.text_input("Declared invoice total *", key="intake_declared_total")

    st.markdown("### Review invoice line items")
    st.caption("OCR may not reliably identify line tables. Add, remove or correct rows. Values in this table are the user-reviewed values used for system calculations.")
    line_frame = st.session_state.get("intake_lines_frame", _line_frame([]))
    code_options = [""] + [str(code["tax_code"]) for code in tax_codes]
    # Suggest a tax code when the OCR-inferred effective rate matches a configured code.
    if "intake_tax_code_suggestions_applied" not in st.session_state:
        rate_to_code = {round(float(code["tax_rate"]), 4): str(code["tax_code"]) for code in tax_codes}
        suggested_lines = st.session_state.get("intake_lines_frame", _line_frame([])).copy()
        if not suggested_lines.empty and "tax_code" in suggested_lines.columns:
            for row_index in suggested_lines.index:
                current_code = str(suggested_lines.at[row_index, "tax_code"] or "").strip()
                try:
                    current_rate = round(float(suggested_lines.at[row_index, "tax_rate"]), 4)
                except (TypeError, ValueError):
                    current_rate = -1
                if not current_code and current_rate in rate_to_code:
                    suggested_lines.at[row_index, "tax_code"] = rate_to_code[current_rate]
            st.session_state["intake_lines_frame"] = suggested_lines
        st.session_state["intake_tax_code_suggestions_applied"] = True
    edited_lines = st.data_editor(
        line_frame,
        key="intake_line_editor",
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "description": st.column_config.TextColumn("Description", required=True),
            "sku": st.column_config.TextColumn("SKU / item code"),
            "uom": st.column_config.TextColumn("Unit"),
            "quantity": st.column_config.NumberColumn("Quantity", min_value=0.0001, step=1.0, format="%.4f"),
            "unit_price": st.column_config.NumberColumn("Unit price", min_value=0.0, step=1.0, format="%.2f"),
            "tax_code": st.column_config.SelectboxColumn("Tax code", options=code_options),
            "tax_rate": st.column_config.NumberColumn("Tax rate %", min_value=0.0, max_value=100.0, step=0.5, format="%.4f"),
        },
    )

    valid_lines = edited_lines.copy()
    valid_lines["description"] = valid_lines["description"].fillna("").astype(str).str.strip()
    valid_lines = valid_lines[valid_lines["description"] != ""]
    preview_subtotal, preview_tax, preview_total = _preview_totals(valid_lines)
    calc_cols = st.columns(3)
    calc_cols[0].metric("Calculated subtotal", f"{currency} {preview_subtotal:,.2f}")
    calc_cols[1].metric("Calculated tax", f"{currency} {preview_tax:,.2f}")
    calc_cols[2].metric("Calculated total", f"{currency} {preview_total:,.2f}")

    coding_rows: list[dict[str, Any]] = []
    if po_id is None:
        st.markdown("### Manual account coding (required for non-PO invoices)")
        if "intake_coding_frame" not in st.session_state:
            st.session_state["intake_coding_frame"] = pd.DataFrame([{
                "expense_account_code": "",
                "department_code": "",
                "cost_center_code": "",
                "project_code": "",
                "coding_amount": float(preview_total),
                "coding_notes": "",
            }])
        coding_frame = st.data_editor(
            st.session_state["intake_coding_frame"],
            key="intake_coding_editor",
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "expense_account_code": st.column_config.TextColumn("Expense account", required=True),
                "department_code": st.column_config.TextColumn("Department", required=True),
                "cost_center_code": st.column_config.TextColumn("Cost centre", required=True),
                "project_code": st.column_config.TextColumn("Project"),
                "coding_amount": st.column_config.NumberColumn("Coding amount", min_value=0.0, format="%.2f"),
                "coding_notes": st.column_config.TextColumn("Notes"),
            },
        )
        coding_rows = coding_frame.astype(object).where(pd.notna(coding_frame), None).to_dict(orient="records")
        coding_rows = [
            row for row in coding_rows
            if any(str(row.get(field) or "").strip() for field in ("expense_account_code", "department_code", "cost_center_code", "project_code"))
            or _ui_decimal(row.get("coding_amount")) > 0
        ]
        coding_total = sum((_ui_decimal(row.get("coding_amount")) for row in coding_rows), Decimal("0.00"))
        st.caption(f"Coding allocation total: {currency} {coding_total:,.2f}. It must equal the calculated invoice total for this initial workflow.")

    saved_id = st.session_state.get("intake_saved_invoice_id")
    if saved_id:
        st.success(f"Invoice {saved_id} was saved successfully.")
        if st.button("Start another invoice", key="intake_new_invoice"):
            for key in list(st.session_state.keys()):
                if key.startswith("intake_"):
                    del st.session_state[key]
            st.rerun()
        return

    if st.button("Save reviewed invoice to MySQL", type="primary", key="intake_save_invoice"):
        try:
            parsed_subtotal = _decimal_or_none(declared_subtotal_text)
            parsed_tax = _decimal_or_none(declared_tax_text)
            parsed_total = _decimal_or_none(declared_total_text)
            if declared_total_text.strip() and parsed_total is None:
                raise ValueError("Declared invoice total is not a valid number.")
            line_records = valid_lines.astype(object).where(pd.notna(valid_lines), None).to_dict(orient="records")
            if not line_records:
                raise ValueError("Add at least one invoice line with a description.")
            result = save_reviewed_invoice(
                file_bytes=file_bytes,
                filename=uploaded.name,
                vendor_id=vendor_id,
                supplier_invoice_number=supplier_number,
                invoice_date=invoice_date,
                due_date=due_date,
                po_id=po_id,
                currency_code=currency,
                declared_subtotal=parsed_subtotal,
                declared_tax=parsed_tax,
                declared_total=parsed_total,
                lines=line_records,
                submitted_by=submitted_by,
                extraction_succeeded=bool(raw_text.strip()),
                non_po_codings=coding_rows,
                raw_text=raw_text,
            )
            st.session_state["intake_saved_invoice_id"] = result["invoice_id"]
            st.success(f"Invoice saved: {result['invoice_id']}")
            st.json(result)
        except Exception as exc:
            st.error(f"Invoice was not saved: {exc}")
