from decimal import Decimal


def test_money_tolerance_is_decimal_safe():
    assert abs(Decimal("100.00") - Decimal("100.01")) == Decimal("0.01")


def test_project_package_imports():
    import database.connection  # noqa: F401
    import src.services.invoice_service  # noqa: F401
    import src.matching.matching_service  # noqa: F401


def test_invoice_parser_extracts_header_fields_without_confusing_due_date():
    from datetime import date
    from decimal import Decimal
    from src.ocr.parser import parse_invoice_text

    sample = """BRIGHTSTAR TECHNOLOGIES\nInvoice Date: 13/10/2026\nDue Date: 12/11/2026\nInvoice Number: BRIGHT-INV-1001\nPO Number: PO-2026-000001\nSubtotal: INR 10,000.00\nTotal Tax: INR 1,800.00\nGrand Total: INR 11,800.00\n"""
    parsed = parse_invoice_text(sample)
    assert parsed["supplier_invoice_number"] == "BRIGHT-INV-1001"
    assert parsed["invoice_date"] == date(2026, 10, 13)
    assert parsed["due_date"] == date(2026, 11, 12)
    assert parsed["po_reference"] == "PO-2026-000001"
    assert parsed["declared_subtotal"] == Decimal("10000.00")
    assert parsed["declared_tax"] == Decimal("1800.00")
    assert parsed["declared_total"] == Decimal("11800.00")


def test_invoice_parser_handles_missing_fields_conservatively():
    from src.ocr.parser import parse_invoice_text

    parsed = parse_invoice_text("A generic document with no invoice labels.")
    assert parsed["supplier_invoice_number"] is None
    assert parsed["invoice_date"] is None
    assert parsed["declared_total"] is None
    assert parsed["line_items"] == []


def test_invoice_parser_extracts_simple_lines_and_suggests_effective_tax_rate():
    from src.ocr.parser import parse_invoice_text

    sample = """Invoice Number: INV-TEST-01
Invoice Date: 31/10/2026
Subtotal: INR 42,000.00
Total Tax: INR 7,560.00
Grand Total: INR 49,560.00
Business Laptop 1 40,000.00 40,000.00
Wireless Keyboard 2 1,000.00 2,000.00
"""
    parsed = parse_invoice_text(sample)
    assert len(parsed["line_items"]) == 2
    assert parsed["line_items"][0]["description"] == "Business Laptop"
    assert parsed["line_items"][0]["tax_rate"] == 18.0
