"""Generate deterministic synthetic AP data for Invoice Automation.

By default this writes CSV/JSON files only. Loading into MySQL is opt-in via
--load-mysql; it appends SYN-prefixed records and never deletes demo records.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import shutil
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from faker import Faker

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "data" / "generated" / "seed_20261022"
SEED = 20261022
BASE_DATE = date(2026, 10, 31)
USER_REQUESTER = "USR-00001"
USER_ANALYST = "USR-00002"
USER_APPROVER = "USR-00003"
TAX_CODES = [("GST_0", Decimal("0.0000")), ("GST_5", Decimal("5.0000")),
             ("GST_12", Decimal("12.0000")), ("GST_18", Decimal("18.0000")),
             ("GST_28", Decimal("28.0000"))]
PRODUCTS = [
    ("LAP-001", "Business Laptop", "UNIT", Decimal("40000.00")),
    ("MON-001", "LED Monitor", "UNIT", Decimal("12000.00")),
    ("KEY-001", "Wireless Keyboard", "UNIT", Decimal("1000.00")),
    ("MOU-001", "Wireless Mouse", "UNIT", Decimal("500.00")),
    ("UPS-001", "Power Backup UPS", "UNIT", Decimal("6000.00")),
    ("PRN-001", "Laser Printer", "UNIT", Decimal("9500.00")),
    ("NET-001", "Network Router", "UNIT", Decimal("3500.00")),
    ("DSK-001", "Office Desk", "UNIT", Decimal("8500.00")),
]

TABLE_COLUMNS: dict[str, list[str]] = {
    "vendors": ["vendor_id", "vendor_code", "vendor_name", "tax_registration_no", "email", "phone", "address_line1", "address_line2", "city", "state_province", "postal_code", "country_code", "currency_code", "vendor_status", "created_at", "updated_at"],
    "purchase_orders": ["po_id", "vendor_id", "po_date", "expected_delivery_date", "currency_code", "po_status", "created_by", "created_at", "updated_at"],
    "purchase_order_lines": ["po_line_id", "po_id", "line_number", "sku", "description", "uom", "ordered_quantity", "unit_price", "tax_code", "tax_rate", "created_at"],
    "goods_receipts": ["receipt_id", "po_id", "supplier_delivery_note", "receipt_date", "received_by", "receipt_status", "created_at", "updated_at"],
    "goods_receipt_lines": ["receipt_line_id", "receipt_id", "po_id", "po_line_id", "line_number", "received_quantity", "accepted_quantity", "rejected_quantity", "created_at"],
    "invoices": ["invoice_id", "vendor_id", "po_id", "original_invoice_id", "supplier_invoice_number", "invoice_date", "due_date", "received_at", "currency_code", "declared_subtotal", "declared_tax", "declared_total", "calculated_subtotal", "calculated_tax", "calculated_total", "invoice_status", "match_status", "extraction_status", "source_channel", "source_file_path", "source_file_hash", "submitted_by", "payment_ready", "created_at", "updated_at"],
    "invoice_lines": ["invoice_line_id", "invoice_id", "po_id", "line_number", "sku", "description", "uom", "quantity", "unit_price", "tax_code", "tax_rate", "declared_line_subtotal", "declared_tax_amount", "declared_line_total", "created_at"],
    "invoice_po_line_matches": ["match_id", "invoice_id", "po_id", "invoice_line_id", "po_line_id", "receipt_line_id", "matched_quantity", "match_result", "created_at"],
    "invoice_account_codings": ["coding_id", "invoice_id", "invoice_line_id", "expense_account_code", "department_code", "cost_center_code", "project_code", "coding_amount", "currency_code", "coded_by", "coding_status", "coding_notes", "created_at", "updated_at"],
    "approvals": ["approval_id", "invoice_id", "approval_level", "approver_id", "approval_status", "assigned_at", "decision_at", "comments", "created_at", "updated_at"],
    "payments": ["payment_id", "invoice_id", "approval_id", "payment_reference", "payment_amount", "currency_code", "payment_date", "payment_method", "bank_reference", "payment_status", "processed_by", "created_at", "updated_at"],
    "invoice_exceptions": ["exception_id", "invoice_id", "issue_type", "severity", "exception_status", "is_blocking", "description", "assigned_to", "resolved_by", "detected_at", "resolved_at", "resolution_notes"],
    "invoice_events": ["event_id", "invoice_id", "event_type", "previous_status", "new_status", "actor_id", "event_reason", "event_metadata", "occurred_at"],
    "approval_events": ["event_id", "approval_id", "previous_status", "new_status", "actor_id", "comments", "event_metadata", "occurred_at"],
    "payment_events": ["event_id", "payment_id", "previous_status", "new_status", "actor_id", "comments", "event_metadata", "occurred_at"],
}
LOAD_ORDER = ["vendors", "purchase_orders", "purchase_order_lines", "goods_receipts", "goods_receipt_lines", "invoices", "invoice_lines", "invoice_po_line_matches", "invoice_account_codings", "approvals", "payments", "invoice_exceptions", "invoice_events", "approval_events", "payment_events"]
PK_COLUMNS = {name: cols[0] for name, cols in TABLE_COLUMNS.items()}


def money(value: Decimal | int | float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def dt(value: date, hour: int = 10) -> str:
    return datetime(value.year, value.month, value.day, hour, 0, 0).strftime("%Y-%m-%d %H:%M:%S")


def date_text(value: date | None) -> str | None:
    return value.isoformat() if value else None


def dec(value: Decimal | int | float, places: str = "0.0000") -> str:
    return Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP).to_eng_string()


def make_generator(seed: int) -> tuple[random.Random, Faker]:
    rng = random.Random(seed)
    fake = Faker("en_IN")
    fake.seed_instance(seed)
    return rng, fake


def generate(seed: int = SEED, vendors_n: int = 300, pos_n: int = 1500,
             po_lines_n: int = 2200, receipts_n: int = 1800,
             invoices_n: int = 2000) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    if vendors_n != 300 or pos_n != 1500 or po_lines_n != 2200 or receipts_n != 1800 or invoices_n != 2000:
        # Counts are configurable through the function for tests, but the CLI default
        # preserves the agreed target. This first version expects at least one line/PO.
        if po_lines_n < pos_n or receipts_n < 1 or invoices_n < 1:
            raise ValueError("Require PO lines >= POs and positive receipt/invoice counts.")
    rng, fake = make_generator(seed)
    tables: dict[str, list[dict[str, Any]]] = {name: [] for name in TABLE_COLUMNS}
    ground_truth: list[dict[str, Any]] = []
    now = dt(BASE_DATE)

    # 1) Vendors: 270 active and 30 blocked to create a controlled vendor-control scenario.
    vendors: list[dict[str, Any]] = []
    for i in range(1, vendors_n + 1):
        status = "BLOCKED" if i > vendors_n - max(1, vendors_n // 10) else "ACTIVE"
        vendor_id = f"SYN-VND-{i:06d}"
        vendor_code = f"SYNV{i:05d}"
        vendor = {
            "vendor_id": vendor_id, "vendor_code": vendor_code,
            "vendor_name": f"{fake.company()} {i:03d}",
            "tax_registration_no": f"SYN-GST-{i:08d}",
            "email": f"accounts{i:04d}@{fake.free_email_domain()}",
            "phone": fake.numerify(text="9#########"),
            "address_line1": fake.street_address()[:200], "address_line2": None,
            "city": fake.city()[:100], "state_province": fake.state()[:100],
            "postal_code": fake.postcode()[:20], "country_code": "IN",
            "currency_code": "INR", "vendor_status": status,
            "created_at": "2026-01-01 09:00:00", "updated_at": now,
        }
        vendors.append(vendor)
    tables["vendors"] = vendors

    # 2) Purchase orders: round-robin vendor assignment ensures all vendors participate.
    po_rows: list[dict[str, Any]] = []
    po_vendor: dict[str, str] = {}
    po_date_map: dict[str, date] = {}
    po_line_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    vendor_by_id = {v["vendor_id"]: v for v in vendors}
    line_counts = [1] * pos_n
    # For the target 1,500 POs / 2,200 lines, 700 POs get two lines and 800 get one.
    if po_lines_n == 2200 and pos_n == 1500:
        for idx in rng.sample(range(pos_n), 700):
            line_counts[idx] = 2
    else:
        remaining = po_lines_n - pos_n
        for idx in rng.sample(range(pos_n), min(remaining, pos_n)):
            line_counts[idx] += 1
        # distribute additional lines if a non-default test count requests them
        for _ in range(max(0, remaining - pos_n)):
            line_counts[rng.randrange(pos_n)] += 1
    for n in range(1, pos_n + 1):
        po_id = f"SYN-PO-2026-{n:06d}"
        vendor = vendors[(n - 1) % vendors_n]
        pdate = date(2026, 1, 1) + timedelta(days=rng.randint(0, 270))
        po_vendor[po_id] = vendor["vendor_id"]
        po_date_map[po_id] = pdate
        po_rows.append({
            "po_id": po_id, "vendor_id": vendor["vendor_id"],
            "po_date": date_text(pdate),
            "expected_delivery_date": date_text(pdate + timedelta(days=rng.randint(7, 35))),
            "currency_code": "INR", "po_status": "OPEN",
            "created_by": USER_REQUESTER,
            "created_at": dt(pdate), "updated_at": now,
        })
    tables["purchase_orders"] = po_rows

    # 3) PO lines: exact target count, with Decimal-safe financial values.
    line_number_by_po: dict[str, int] = {}
    line_rows: list[dict[str, Any]] = []
    tax_by_code = dict(TAX_CODES)
    for n, po in enumerate(po_rows, start=1):
        po_id = po["po_id"]
        for line_no in range(1, line_counts[n - 1] + 1):
            sku, description, uom, price = rng.choice(PRODUCTS)
            tax_code, rate = rng.choice(TAX_CODES[1:])
            ordered = rng.randint(10, 60)
            po_line_id = f"SYN-POL-2026-{n:06d}-{line_no:02d}"
            row = {
                "po_line_id": po_line_id, "po_id": po_id,
                "line_number": line_no, "sku": sku, "description": description,
                "uom": uom, "ordered_quantity": dec(ordered),
                "unit_price": dec(price, "0.01"), "tax_code": tax_code,
                "tax_rate": dec(rate), "created_at": po["created_at"],
            }
            line_rows.append(row)
            po_line_map[po_id].append(row)
    if len(line_rows) != po_lines_n:
        raise AssertionError(f"PO line count mismatch: {len(line_rows)} != {po_lines_n}")
    tables["purchase_order_lines"] = line_rows

    # 4) Goods receipts: exactly 1,200 POs receive a first receipt; 600 of those
    # receive a second receipt, yielding exactly 1,800 receipt headers.
    active_receipt_pos = [p["po_id"] for p in po_rows if vendor_by_id[po_vendor[p["po_id"]]]["vendor_status"] == "ACTIVE"][:1170]
    blocked_receipt_pos = [p["po_id"] for p in po_rows if vendor_by_id[po_vendor[p["po_id"]]]["vendor_status"] == "BLOCKED"][:30]
    receipt_pos = active_receipt_pos + blocked_receipt_pos
    if len(receipt_pos) < 1200:
        raise ValueError("Not enough receipt-backed POs for the configured vendor split.")
    receipt_pos = receipt_pos[:1200]
    split_pos = set(rng.sample(receipt_pos, min(600, len(receipt_pos))))
    receipt_rows: list[dict[str, Any]] = []
    receipt_line_rows: list[dict[str, Any]] = []
    receipt_lines_by_po_line: dict[str, list[dict[str, Any]]] = defaultdict(list)
    receipt_counter = 0
    for po_id in receipt_pos:
        pdate = po_date_map[po_id]
        lines = po_line_map[po_id]
        # First receipt contains all PO lines.
        receipt_counter += 1
        receipt_id = f"SYN-GRN-2026-{receipt_counter:06d}"
        rdate = min(BASE_DATE, pdate + timedelta(days=rng.randint(2, 25)))
        receipt_rows.append({
            "receipt_id": receipt_id, "po_id": po_id,
            "supplier_delivery_note": f"SYN-DN-{receipt_counter:06d}",
            "receipt_date": date_text(rdate), "received_by": USER_ANALYST,
            "receipt_status": "POSTED", "created_at": dt(rdate), "updated_at": dt(rdate),
        })
        for line_idx, pol in enumerate(lines, start=1):
            ordered = Decimal(pol["ordered_quantity"])
            if po_id in split_pos:
                first_received = max(Decimal("1.0000"), (ordered * Decimal("0.60")).quantize(Decimal("0.0001")))
            else:
                first_received = (ordered * Decimal(str(rng.uniform(0.65, 1.0)))).quantize(Decimal("0.0001"))
            first_rejected = Decimal("0.0000") if rng.random() < 0.9 else Decimal("1.0000")
            first_rejected = min(first_rejected, max(Decimal("0.0000"), first_received - Decimal("1.0000")))
            first_accepted = first_received - first_rejected
            grl_id = f"SYN-GRL-{receipt_counter:06d}-{line_idx:02d}-01"
            grl = {
                "receipt_line_id": grl_id, "receipt_id": receipt_id, "po_id": po_id,
                "po_line_id": pol["po_line_id"], "line_number": line_idx,
                "received_quantity": dec(first_received), "accepted_quantity": dec(first_accepted),
                "rejected_quantity": dec(first_rejected), "created_at": dt(rdate),
            }
            receipt_line_rows.append(grl)
            receipt_lines_by_po_line[pol["po_line_id"]].append(grl)
            if po_id in split_pos:
                second_received = ordered - first_received
                if second_received > 0:
                    # The second receipt is added later under a separate receipt header.
                    pol.setdefault("_second_received", dec(second_received))
        if po_id in split_pos:
            receipt_counter += 1
            receipt_id_2 = f"SYN-GRN-2026-{receipt_counter:06d}"
            rdate2 = min(BASE_DATE, rdate + timedelta(days=rng.randint(1, 8)))
            receipt_rows.append({
                "receipt_id": receipt_id_2, "po_id": po_id,
                "supplier_delivery_note": f"SYN-DN-{receipt_counter:06d}",
                "receipt_date": date_text(rdate2), "received_by": USER_ANALYST,
                "receipt_status": "POSTED", "created_at": dt(rdate2), "updated_at": dt(rdate2),
            })
            for line_idx, pol in enumerate(lines, start=1):
                second_text = pol.pop("_second_received", None)
                if not second_text:
                    continue
                second_received = Decimal(second_text)
                grl_id = f"SYN-GRL-{receipt_counter:06d}-{line_idx:02d}-02"
                grl = {
                    "receipt_line_id": grl_id, "receipt_id": receipt_id_2, "po_id": po_id,
                    "po_line_id": pol["po_line_id"], "line_number": line_idx,
                    "received_quantity": dec(second_received),
                    "accepted_quantity": dec(second_received), "rejected_quantity": dec(0),
                    "created_at": dt(rdate2),
                }
                receipt_line_rows.append(grl)
                receipt_lines_by_po_line[pol["po_line_id"]].append(grl)
    if len(receipt_rows) != receipts_n:
        raise AssertionError(f"Goods receipt header count mismatch: {len(receipt_rows)} != {receipts_n}")
    tables["goods_receipts"] = receipt_rows
    tables["goods_receipt_lines"] = receipt_line_rows

    # Reflect receipt progress in PO headers.
    received_by_po: dict[str, Decimal] = defaultdict(Decimal)
    for grl in receipt_line_rows:
        received_by_po[grl["po_id"]] += Decimal(grl["received_quantity"])
    for po in po_rows:
        line_total = sum(Decimal(x["ordered_quantity"]) for x in po_line_map[po["po_id"]])
        received_total = received_by_po[po["po_id"]]
        po["po_status"] = "OPEN" if received_total == 0 else ("CLOSED" if received_total >= line_total else "PARTIALLY_RECEIVED")

    # 5) Select unique PO-line candidates by scenario.
    active_valid: list[dict[str, Any]] = []
    blocked_valid: list[dict[str, Any]] = []
    no_receipt_lines: list[dict[str, Any]] = []
    for pol in line_rows:
        vendor = vendor_by_id[po_vendor[pol["po_id"]]]
        grls = receipt_lines_by_po_line.get(pol["po_line_id"], [])
        candidate = {"pol": pol, "vendor": vendor, "grls": grls}
        if grls and vendor["vendor_status"] == "ACTIVE":
            active_valid.append(candidate)
        elif grls and vendor["vendor_status"] == "BLOCKED":
            blocked_valid.append(candidate)
        elif not grls and vendor["vendor_status"] == "ACTIVE":
            no_receipt_lines.append(candidate)
    required_active = 1100 + 200 + 160 + 30
    if len(active_valid) < required_active or len(blocked_valid) < 30 or len(no_receipt_lines) < 80:
        raise ValueError(f"Insufficient scenario candidates: active={len(active_valid)}, blocked={len(blocked_valid)}, no_receipt={len(no_receipt_lines)}")
    rng.shuffle(active_valid)
    rng.shuffle(blocked_valid)
    rng.shuffle(no_receipt_lines)
    clean_candidates = active_valid[:1100]
    price_candidates = active_valid[1100:1300]
    quantity_candidates = active_valid[1300:1460]
    tax_candidates = active_valid[1460:1490]
    blocked_candidates = blocked_valid[:30]
    missing_candidates = no_receipt_lines[:80]

    invoices: list[dict[str, Any]] = []
    invoice_lines: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    codings: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []
    payments: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    invoice_events: list[dict[str, Any]] = []
    approval_events: list[dict[str, Any]] = []
    payment_events: list[dict[str, Any]] = []
    id_counters = Counter()
    invoice_meta: dict[str, dict[str, Any]] = {}
    vendor_invoice_seq = Counter()
    scenario_counts = Counter()
    payment_ref_counter = 0

    def next_id(prefix: str) -> str:
        id_counters[prefix] += 1
        return f"SYN-{prefix}-2026-{id_counters[prefix]:06d}"

    def add_exception(invoice_id: str, issue: str, severity: str, description: str) -> None:
        exceptions.append({
            "exception_id": next_id("EXC"), "invoice_id": invoice_id,
            "issue_type": issue, "severity": severity, "exception_status": "OPEN",
            "is_blocking": 1, "description": description, "assigned_to": USER_ANALYST,
            "resolved_by": None, "detected_at": dt(BASE_DATE, 12),
            "resolved_at": None, "resolution_notes": None,
        })

    def create_invoice(candidate: dict[str, Any] | None, scenario: str, *, non_po: bool = False,
                       original: dict[str, Any] | None = None) -> dict[str, Any]:
        nonlocal payment_ref_counter
        invoice_id = next_id("INV")
        if original:
            vendor = original["vendor"]
            po_id = original["po_id"]
            supplier_number = original["supplier_invoice_number"]
            invoice_date = date.fromisoformat(original["invoice_date"])
            line_source = original["line_source"]
            subtotal = Decimal(original["subtotal"])
            tax = Decimal(original["tax"])
            total = Decimal(original["total"])
            invoice_status, match_status = "PENDING_EXCEPTION", "PENDING"
            due_date = invoice_date + timedelta(days=30)
            original_invoice_id = original["invoice_id"]
        else:
            vendor = candidate["vendor"] if candidate else rng.choice([v for v in vendors if v["vendor_status"] == "ACTIVE"])
            po_id = None if non_po else candidate["pol"]["po_id"]
            if po_id:
                vendor_invoice_seq[vendor["vendor_id"]] += 1
                supplier_number = f"{vendor['vendor_code']}-INV-{vendor_invoice_seq[vendor['vendor_id']]:06d}"
                pdate = po_date_map[po_id]
                invoice_date = min(BASE_DATE, pdate + timedelta(days=rng.randint(5, 55)))
            else:
                vendor_invoice_seq[vendor["vendor_id"]] += 1
                supplier_number = f"{vendor['vendor_code']}-NPO-{vendor_invoice_seq[vendor['vendor_id']]:06d}"
                invoice_date = date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))
            due_date = invoice_date + timedelta(days=30)
            original_invoice_id = None
            if non_po:
                sku, desc, uom, unit = ("SRV-001", rng.choice(["Monthly Internet Service", "Office Electricity", "Facility Maintenance"]), "MONTH", Decimal(rng.randrange(5000, 25000, 500)))
                qty = Decimal("1.0000")
                tax_code, tax_rate = rng.choice(TAX_CODES[1:])
                line_source = {"po_line_id": None, "receipt_lines": [], "sku": sku, "description": desc, "uom": uom, "quantity": qty, "unit_price": unit, "tax_code": tax_code, "tax_rate": tax_rate, "matchable": False}
            else:
                pol = candidate["pol"]
                grls = candidate["grls"]
                ordered = Decimal(pol["ordered_quantity"])
                accepted_total = sum(Decimal(g["accepted_quantity"]) for g in grls)
                if scenario == "QUANTITY_MISMATCH":
                    qty = accepted_total + Decimal("1.0000")
                elif scenario == "MISSING_RECEIPT":
                    qty = Decimal(rng.randint(1, 3))
                else:
                    qty = min(Decimal(rng.randint(1, 3)), max(Decimal("1.0000"), Decimal(grls[0]["accepted_quantity"])))
                unit = Decimal(pol["unit_price"])
                tax_code = pol["tax_code"]
                tax_rate = Decimal(pol["tax_rate"])
                if scenario == "PRICE_VARIANCE":
                    unit = money(unit * Decimal("1.05"))
                if scenario == "TAX_MISMATCH":
                    tax_code = "GST_28" if tax_code != "GST_28" else "GST_12"
                    tax_rate = Decimal("28.0000") if tax_code == "GST_28" else Decimal("12.0000")
                line_source = {"po_line_id": pol["po_line_id"], "receipt_lines": grls,
                               "sku": pol["sku"], "description": pol["description"], "uom": pol["uom"],
                               "quantity": qty, "unit_price": unit, "tax_code": tax_code,
                               "tax_rate": tax_rate, "matchable": bool(grls)}
            subtotal = money(line_source["quantity"] * line_source["unit_price"])
            tax = money(subtotal * Decimal(line_source["tax_rate"]) / Decimal("100"))
            total = money(subtotal + tax)
            if scenario in ("CLEAN_PO", "CLEAN_NON_PO"):
                invoice_status = "APPROVED" if rng.random() < 0.70 else "PENDING_APPROVAL"
                match_status = "NOT_APPLICABLE" if non_po else "MATCHED"
            elif scenario == "MISSING_RECEIPT":
                invoice_status, match_status = "PENDING_EXCEPTION", "VARIANCE"
            elif scenario == "VENDOR_BLOCKED":
                invoice_status, match_status = "PENDING_EXCEPTION", "MATCHED"
            else:
                invoice_status, match_status = "PENDING_EXCEPTION", "VARIANCE"
        line_no = 1
        invoice_line_id = f"SYN-INVL-2026-{id_counters['INV']:06d}-01"
        line_source = dict(line_source)
        qty = Decimal(line_source["quantity"])
        unit = Decimal(line_source["unit_price"])
        rate = Decimal(line_source["tax_rate"])
        line_sub = money(qty * unit)
        line_tax = money(line_sub * rate / Decimal("100"))
        line_total = money(line_sub + line_tax)
        invoices.append({
            "invoice_id": invoice_id, "vendor_id": vendor["vendor_id"], "po_id": po_id,
            "original_invoice_id": original_invoice_id, "supplier_invoice_number": supplier_number,
            "invoice_date": date_text(invoice_date), "due_date": date_text(due_date),
            "received_at": dt(invoice_date + timedelta(days=1)), "currency_code": "INR",
            "declared_subtotal": dec(subtotal, "0.01"), "declared_tax": dec(tax, "0.01"),
            "declared_total": dec(total, "0.01"), "calculated_subtotal": dec(subtotal, "0.01"),
            "calculated_tax": dec(tax, "0.01"), "calculated_total": dec(total, "0.01"),
            "invoice_status": invoice_status, "match_status": match_status,
            "extraction_status": "SUCCEEDED", "source_channel": rng.choice(["EMAIL", "PORTAL", "API"]),
            "source_file_path": None, "source_file_hash": None,
            "submitted_by": USER_REQUESTER, "payment_ready": int(invoice_status == "APPROVED"),
            "created_at": dt(invoice_date + timedelta(days=1)), "updated_at": now,
        })
        invoice_lines.append({
            "invoice_line_id": invoice_line_id, "invoice_id": invoice_id, "po_id": po_id,
            "line_number": line_no, "sku": line_source["sku"], "description": line_source["description"],
            "uom": line_source["uom"], "quantity": dec(qty), "unit_price": dec(unit, "0.01"),
            "tax_code": line_source["tax_code"], "tax_rate": dec(rate),
            "declared_line_subtotal": dec(line_sub, "0.01"),
            "declared_tax_amount": dec(line_tax, "0.01"),
            "declared_line_total": dec(line_total, "0.01"), "created_at": dt(invoice_date + timedelta(days=1)),
        })
        meta = {"invoice_id": invoice_id, "vendor": vendor, "po_id": po_id,
                "supplier_invoice_number": supplier_number, "invoice_date": date_text(invoice_date),
                "subtotal": dec(subtotal, "0.01"), "tax": dec(tax, "0.01"), "total": dec(total, "0.01"),
                "line_source": line_source, "invoice_line_id": invoice_line_id,
                "invoice_status": invoice_status, "match_status": match_status}
        invoice_meta[invoice_id] = meta
        scenario_counts[scenario] += 1
        ground_truth.append({"invoice_id": invoice_id, "primary_scenario": scenario,
                             "expected_blocking": int(scenario not in ("CLEAN_PO", "CLEAN_NON_PO")),
                             "expected_match_status": match_status,
                             "expected_invoice_status": invoice_status})

        # Matching allocations: allocate invoice qty across valid receipt rows.
        if po_id and line_source["matchable"] and scenario not in ("MISSING_RECEIPT", "DUPLICATE"):
            remaining = qty
            receipt_sequence = 0
            for grl in line_source["receipt_lines"]:
                available = Decimal(grl["accepted_quantity"])
                allocate = min(available, remaining)
                if allocate > 0:
                    receipt_sequence += 1
                    matches.append({
                        "match_id": next_id("MATCH"), "invoice_id": invoice_id,
                        "po_id": po_id, "invoice_line_id": invoice_line_id,
                        "po_line_id": line_source["po_line_id"],
                        "receipt_line_id": grl["receipt_line_id"],
                        "matched_quantity": dec(allocate),
                        "match_result": "VARIANCE" if scenario in ("PRICE_VARIANCE", "QUANTITY_MISMATCH", "TAX_MISMATCH") else "MATCHED",
                        "created_at": dt(invoice_date + timedelta(days=1)),
                    })
                    remaining -= allocate
                if remaining <= 0:
                    break
        if non_po:
            coding_status = "APPROVED" if invoice_status == "APPROVED" else "SUBMITTED"
            codings.append({
                "coding_id": next_id("COD"), "invoice_id": invoice_id,
                "invoice_line_id": invoice_line_id,
                "expense_account_code": "INTERNET_EXPENSE" if "Internet" in line_source["description"] else "FACILITY_EXPENSE",
                "department_code": "IT" if "Internet" in line_source["description"] else "FACILITIES",
                "cost_center_code": "CC-205", "project_code": "SYNTHETIC_OPERATIONS",
                "coding_amount": dec(total, "0.01"), "currency_code": "INR",
                "coded_by": USER_ANALYST, "coding_status": coding_status,
                "coding_notes": "Synthetic non-PO expense coding.",
                "created_at": dt(invoice_date + timedelta(days=1)), "updated_at": now,
            })

        if invoice_status in ("APPROVED", "PENDING_APPROVAL"):
            approval_id = next_id("APR")
            approval_status = "APPROVED" if invoice_status == "APPROVED" else "PENDING"
            decision = dt(invoice_date + timedelta(days=3)) if approval_status == "APPROVED" else None
            approvals.append({
                "approval_id": approval_id, "invoice_id": invoice_id, "approval_level": 1,
                "approver_id": USER_APPROVER, "approval_status": approval_status,
                "assigned_at": dt(invoice_date + timedelta(days=2)), "decision_at": decision,
                "comments": "Synthetic dataset approval." if decision else "Awaiting synthetic approval decision.",
                "created_at": dt(invoice_date + timedelta(days=2)), "updated_at": now,
            })
            approval_events.append({
                "event_id": next_id("APEVT"), "approval_id": approval_id,
                "previous_status": None, "new_status": "PENDING", "actor_id": USER_ANALYST,
                "comments": "Approval assigned in synthetic dataset.", "event_metadata": None,
                "occurred_at": dt(invoice_date + timedelta(days=2)),
            })
            if approval_status == "APPROVED":
                approval_events.append({
                    "event_id": next_id("APEVT"), "approval_id": approval_id,
                    "previous_status": "PENDING", "new_status": "APPROVED", "actor_id": USER_APPROVER,
                    "comments": "Synthetic approval decision.", "event_metadata": None,
                    "occurred_at": decision,
                })
                # Some approved invoices are paid fully or partially.
                if rng.random() < 0.20:
                    payment_ref_counter += 1
                    full = rng.random() < 0.25
                    amount = total if full else money(total * Decimal("0.40"))
                    payment_id = next_id("PAY")
                    pay_date = invoice_date + timedelta(days=7)
                    payments.append({
                        "payment_id": payment_id, "invoice_id": invoice_id,
                        "approval_id": approval_id,
                        "payment_reference": f"SYN-PAYREF-{payment_ref_counter:08d}",
                        "payment_amount": dec(amount, "0.01"), "currency_code": "INR",
                        "payment_date": date_text(pay_date), "payment_method": rng.choice(["NEFT", "RTGS", "IMPS", "UPI"]),
                        "bank_reference": f"SYN-BANK-{payment_ref_counter:08d}",
                        "payment_status": "PAID", "processed_by": USER_ANALYST,
                        "created_at": dt(pay_date), "updated_at": dt(pay_date),
                    })
                    if full:
                        invoices[-1]["invoice_status"] = "PAID"
                        invoices[-1]["payment_ready"] = 0
                        invoice_meta[invoice_id]["invoice_status"] = "PAID"
                    else:
                        invoices[-1]["invoice_status"] = "PARTIALLY_PAID"
                        invoices[-1]["payment_ready"] = 1
                        invoice_meta[invoice_id]["invoice_status"] = "PARTIALLY_PAID"
                    for prev_status, new_status, ev_index in [(None, "PENDING", 1), ("PENDING", "PROCESSING", 2), ("PROCESSING", "PAID", 3)]:
                        payment_events.append({
                            "event_id": next_id("PEVT"), "payment_id": payment_id,
                            "previous_status": prev_status, "new_status": new_status,
                            "actor_id": USER_ANALYST, "comments": "Synthetic payment reported as completed.",
                            "event_metadata": None, "occurred_at": dt(pay_date, 12 + ev_index),
                        })

        # Every invoice receives an auditable initial/final generation event.
        invoice_events.append({
            "event_id": next_id("IEVT"), "invoice_id": invoice_id,
            "event_type": "DATASET_GENERATED", "previous_status": None,
            "new_status": invoices[-1]["invoice_status"], "actor_id": USER_ANALYST,
            "event_reason": f"Synthetic invoice generated; scenario={scenario}.",
            "event_metadata": json.dumps({"seed": seed, "scenario": scenario}),
            "occurred_at": dt(invoice_date + timedelta(days=1)),
        })

        if scenario == "PRICE_VARIANCE":
            add_exception(invoice_id, "PRICE_VARIANCE", "MEDIUM", "Invoice unit price differs from the purchase order price by approximately 5%.")
        elif scenario == "QUANTITY_MISMATCH":
            add_exception(invoice_id, "QUANTITY_MISMATCH", "HIGH", "Invoice quantity exceeds accepted goods receipt quantity for the PO line.")
        elif scenario == "MISSING_RECEIPT":
            add_exception(invoice_id, "MISSING_RECEIPT", "HIGH", "No posted goods receipt exists for the invoiced PO line.")
        elif scenario == "TAX_MISMATCH":
            add_exception(invoice_id, "TAX_MISMATCH", "HIGH", "Invoice tax code/rate differs from the purchase order tax treatment.")
        elif scenario == "VENDOR_BLOCKED":
            add_exception(invoice_id, "VENDOR_BLOCKED", "CRITICAL", "Vendor is blocked; invoice processing and payment are not permitted.")
        return meta

    original_clean_meta: list[dict[str, Any]] = []
    # 1,100 clean PO-backed invoices.
    for cand in clean_candidates:
        original_clean_meta.append(create_invoice(cand, "CLEAN_PO"))
    # 300 clean non-PO invoices with balanced manual coding.
    for _ in range(300):
        create_invoice(None, "CLEAN_NON_PO", non_po=True)
    # Variance scenarios.
    for cand in price_candidates:
        create_invoice(cand, "PRICE_VARIANCE")
    for cand in quantity_candidates:
        create_invoice(cand, "QUANTITY_MISMATCH")
    for cand in missing_candidates:
        cand = dict(cand)
        cand["grls"] = []
        create_invoice(cand, "MISSING_RECEIPT")
    for cand in tax_candidates:
        create_invoice(cand, "TAX_MISMATCH")
    for cand in blocked_candidates:
        create_invoice(cand, "VENDOR_BLOCKED")
    # 100 duplicate submissions: separate invoice records referencing original clean invoices.
    for original in rng.sample(original_clean_meta, 100):
        duplicate_meta = create_invoice(None, "DUPLICATE", original=original)
        add_exception(duplicate_meta["invoice_id"], "DUPLICATE_INVOICE", "CRITICAL",
                      f"Duplicate supplier invoice number {duplicate_meta['supplier_invoice_number']} references original invoice {original['invoice_id']}.")

    if len(invoices) != invoices_n:
        raise AssertionError(f"Invoice count mismatch: {len(invoices)} != {invoices_n}")
    final_status_by_invoice = {row["invoice_id"]: row["invoice_status"] for row in invoices}
    for label in ground_truth:
        label["expected_invoice_status"] = final_status_by_invoice[label["invoice_id"]]

    tables.update({
        "invoices": invoices, "invoice_lines": invoice_lines,
        "invoice_po_line_matches": matches, "invoice_account_codings": codings,
        "approvals": approvals, "payments": payments,
        "invoice_exceptions": exceptions, "invoice_events": invoice_events,
        "approval_events": approval_events, "payment_events": payment_events,
    })
    return tables, ground_truth


def validate(tables: dict[str, list[dict[str, Any]]], truth: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    counts = {name: len(rows) for name, rows in tables.items()}
    # Primary-key uniqueness.
    for table, rows in tables.items():
        pk = PK_COLUMNS[table]
        values = [row.get(pk) for row in rows]
        if len(values) != len(set(values)):
            errors.append(f"Duplicate primary key in {table}.{pk}")
    # Important relationship checks across generated rows.
    vendors = {r["vendor_id"] for r in tables["vendors"]}
    pos = {r["po_id"]: r for r in tables["purchase_orders"]}
    pols = {r["po_line_id"]: r for r in tables["purchase_order_lines"]}
    receipts = {r["receipt_id"]: r for r in tables["goods_receipts"]}
    grls = {r["receipt_line_id"]: r for r in tables["goods_receipt_lines"]}
    invoices = {r["invoice_id"]: r for r in tables["invoices"]}
    inv_lines = {r["invoice_line_id"]: r for r in tables["invoice_lines"]}
    approvals = {r["approval_id"]: r for r in tables["approvals"]}
    for po in tables["purchase_orders"]:
        if po["vendor_id"] not in vendors: errors.append("PO references missing vendor")
    for line in tables["purchase_order_lines"]:
        if line["po_id"] not in pos: errors.append("PO line references missing PO")
        if Decimal(line["ordered_quantity"]) <= 0: errors.append("Non-positive PO quantity")
    for receipt in tables["goods_receipts"]:
        if receipt["po_id"] not in pos: errors.append("Receipt references missing PO")
    for line in tables["goods_receipt_lines"]:
        if line["receipt_id"] not in receipts or line["po_line_id"] not in pols: errors.append("Receipt line has missing parent")
        if receipts[line["receipt_id"]]["po_id"] != line["po_id"] or pols[line["po_line_id"]]["po_id"] != line["po_id"]:
            errors.append("Receipt line PO consistency violation")
        if Decimal(line["received_quantity"]) != Decimal(line["accepted_quantity"]) + Decimal(line["rejected_quantity"]):
            errors.append("Receipt quantity does not reconcile")
    for inv in tables["invoices"]:
        if inv["vendor_id"] not in vendors: errors.append("Invoice references missing vendor")
        if inv["po_id"] and (inv["po_id"] not in pos or pos[inv["po_id"]]["vendor_id"] != inv["vendor_id"]):
            errors.append("Invoice PO/vendor consistency violation")
        if inv["original_invoice_id"] and inv["original_invoice_id"] not in invoices:
            errors.append("Duplicate invoice references missing original")
        calc = money(Decimal(inv["calculated_subtotal"]) + Decimal(inv["calculated_tax"]))
        if calc != Decimal(inv["calculated_total"]): errors.append("Invoice header arithmetic mismatch")
        if inv["payment_ready"] == 1 and inv["invoice_status"] not in ("APPROVED", "PARTIALLY_PAID"):
            errors.append("Non-approved invoice marked payment-ready")
    for line in tables["invoice_lines"]:
        if line["invoice_id"] not in invoices: errors.append("Invoice line references missing invoice")
        if invoices[line["invoice_id"]]["po_id"] != line["po_id"]: errors.append("Invoice line PO differs from invoice header")
    for match in tables["invoice_po_line_matches"]:
        if match["invoice_id"] not in invoices or match["invoice_line_id"] not in inv_lines or match["po_line_id"] not in pols:
            errors.append("Matching record has missing parent")
        if match["receipt_line_id"] is not None:
            if match["receipt_line_id"] not in grls: errors.append("Match references missing receipt line")
            else:
                grl = grls[match["receipt_line_id"]]
                if grl["po_line_id"] != match["po_line_id"] or grl["po_id"] != match["po_id"]:
                    errors.append("Match receipt/PO-line consistency violation")
    for payment in tables["payments"]:
        if payment["invoice_id"] not in invoices or payment["approval_id"] not in approvals:
            errors.append("Payment references missing invoice/approval")
        elif approvals[payment["approval_id"]]["invoice_id"] != payment["invoice_id"] or approvals[payment["approval_id"]]["approval_status"] != "APPROVED":
            errors.append("Payment does not reference an approved approval for same invoice")
    scenario_counts = dict(Counter(r["primary_scenario"] for r in truth))
    expected = {"CLEAN_PO": 1100, "CLEAN_NON_PO": 300, "PRICE_VARIANCE": 200,
                "QUANTITY_MISMATCH": 160, "DUPLICATE": 100,
                "MISSING_RECEIPT": 80, "TAX_MISMATCH": 30, "VENDOR_BLOCKED": 30}
    for key, value in expected.items():
        if scenario_counts.get(key, 0) != value:
            errors.append(f"Scenario count {key}: expected {value}, got {scenario_counts.get(key, 0)}")
    return {"passed": not errors, "errors": errors, "counts": counts,
            "invoice_scenario_counts": scenario_counts,
            "expected_invoice_scenario_counts": expected,
            "ground_truth_rows": len(truth)}


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c) for c in columns})


def write_dataset(out_dir: Path, tables: dict[str, list[dict[str, Any]]], truth: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"Output folder already contains files: {out_dir}. Choose another --out folder or remove it first.")
    out_dir.mkdir(parents=True, exist_ok=True)
    file_hashes: dict[str, str] = {}
    for table, rows in tables.items():
        p = out_dir / f"{table}.csv"
        write_csv(p, rows, TABLE_COLUMNS[table])
        file_hashes[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    truth_path = out_dir / "ground_truth.csv"
    write_csv(truth_path, truth, ["invoice_id", "primary_scenario", "expected_blocking", "expected_match_status", "expected_invoice_status"])
    file_hashes[truth_path.name] = hashlib.sha256(truth_path.read_bytes()).hexdigest()
    validation = validate(tables, truth)
    (out_dir / "validation_report.json").write_text(json.dumps(validation, indent=2), encoding="utf-8")
    manifest = {
        "project": "Invoice Automation System", "seed": seed,
        "generated_on": BASE_DATE.isoformat(), "format": "CSV UTF-8 with BOM; decimal values as strings",
        "primary_key_prefix": "SYN-", "master_data_dependencies": {
            "users": [USER_REQUESTER, USER_ANALYST, USER_APPROVER],
            "tax_codes": [c for c, _ in TAX_CODES],
        },
        "counts": validation["counts"], "invoice_scenario_counts": validation["invoice_scenario_counts"],
        "sha256": file_hashes,
        "ground_truth_is_separate": True,
        "note": "Synthetic records only. ground_truth.csv is an evaluation label file; do not use it as a model input feature.",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return validation


def load_mysql(out_dir: Path) -> None:
    """Append dataset CSVs to the configured MySQL DB in one transaction."""
    try:
        from sqlalchemy import text
        from database.connection import get_engine
    except Exception as exc:
        raise RuntimeError("Could not import the project DB connection. Run this command from the project root with venv active.") from exc

    # Read data first, and fail before opening a transaction if the dataset is incomplete.
    data: dict[str, list[dict[str, Any]]] = {}
    for table in LOAD_ORDER:
        p = out_dir / f"{table}.csv"
        if not p.is_file():
            raise FileNotFoundError(f"Missing generated table file: {p}")
        with p.open("r", newline="", encoding="utf-8-sig") as f:
            data[table] = [{k: (v if v != "" else None) for k, v in row.items()} for row in csv.DictReader(f)]

    engine = get_engine()
    with engine.begin() as conn:
        # Confirm master references exist in the target database.
        user_ids = [USER_REQUESTER, USER_ANALYST, USER_APPROVER]
        existing_users = set(conn.execute(text("SELECT user_id FROM users WHERE user_id IN (:a,:b,:c)"),
                                          {"a": user_ids[0], "b": user_ids[1], "c": user_ids[2]}).scalars())
        if set(user_ids) - existing_users:
            raise RuntimeError(f"Required demo users are missing: {sorted(set(user_ids) - existing_users)}")
        tax_ids = [x[0] for x in TAX_CODES]
        existing_taxes = set(conn.execute(text("SELECT tax_code FROM tax_codes WHERE tax_code IN (:a,:b,:c,:d,:e)"),
                                          dict(zip(["a", "b", "c", "d", "e"], tax_ids))).scalars())
        if set(tax_ids) - existing_taxes:
            raise RuntimeError(f"Required tax codes are missing: {sorted(set(tax_ids) - existing_taxes)}")

        # Preflight generated primary keys; never overwrite or delete existing demo rows.
        for table, rows in data.items():
            if not rows:
                continue
            pk = PK_COLUMNS[table]
            ids = [r[pk] for r in rows]
            # All generated IDs use SYN-. An existing ID is a safe-to-abort collision.
            existing = conn.execute(text(f"SELECT `{pk}` FROM `{table}` WHERE `{pk}` IN :ids".replace("IN :ids", "IN (" + ",".join(f":p{i}" for i in range(len(ids))) + ")")),
                                    {f"p{i}": x for i, x in enumerate(ids)}).scalars().first()
            if existing:
                raise RuntimeError(f"Import stopped: {table}.{pk} already contains generated ID {existing}. No rows were imported.")

        for table in LOAD_ORDER:
            rows = data[table]
            if not rows:
                continue
            # Self-referencing duplicate invoices are inserted after their originals.
            if table == "invoices":
                rows = [r for r in rows if not r.get("original_invoice_id")] + [r for r in rows if r.get("original_invoice_id")]
            cols = TABLE_COLUMNS[table]
            col_sql = ", ".join(f"`{c}`" for c in cols)
            val_sql = ", ".join(f":{c}" for c in cols)
            stmt = text(f"INSERT INTO `{table}` ({col_sql}) VALUES ({val_sql})")
            for start in range(0, len(rows), 500):
                conn.execute(stmt, rows[start:start + 500])
            print(f"Loaded {len(rows):,} rows into {table}")
    print("\nMySQL import committed successfully. Existing non-SYN demo records were not modified.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic synthetic Invoice Automation dataset.")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--overwrite", action="store_true", help="Remove and recreate the output folder first.")
    parser.add_argument("--load-mysql", action="store_true", help="Append generated records to MySQL (opt-in).")
    args = parser.parse_args()
    out = args.out if args.out.is_absolute() else ROOT / args.out
    if args.overwrite and out.exists():
        shutil.rmtree(out)
    tables, truth = generate(seed=args.seed)
    validation = write_dataset(out, tables, truth, args.seed)
    if not validation["passed"]:
        print(json.dumps(validation, indent=2))
        return 2
    print(f"Dataset generated: {out}")
    print(f"Validation: PASS; {len(truth):,} invoice ground-truth rows")
    print(json.dumps(validation["counts"], indent=2))
    print(json.dumps(validation["invoice_scenario_counts"], indent=2))
    if args.load_mysql:
        load_mysql(out)
    else:
        print("\nCSV generation only. No database records were changed.")
        print("To append this dataset to MySQL, rerun with --load-mysql after checking the CSVs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
