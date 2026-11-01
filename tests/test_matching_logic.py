from src.matching.matching_service import (
    _choose_po_line,
    description_similarity,
    normalise_description,
)


def test_normalise_description_removes_spaces_and_punctuation():
    assert normalise_description("Wireless Keyboard - 2.4 GHz") == "wirelesskeyboard24ghz"


def test_description_similarity_is_one_for_exact_normalised_text():
    assert description_similarity("Business Laptop", "business-laptop") == 1.0


def test_choose_po_line_uses_exact_description_when_sku_is_missing():
    invoice_line = {"sku": None, "description": "Wireless Keyboard"}
    po_lines = [
        {"po_line_id": "POL-1", "sku": "LAP-001", "description": "Business Laptop"},
        {"po_line_id": "POL-2", "sku": "KEY-001", "description": "Wireless Keyboard"},
    ]
    selected, score = _choose_po_line(invoice_line, po_lines)
    assert selected["po_line_id"] == "POL-2"
    assert score == 1.0


def test_choose_po_line_rejects_ambiguous_or_weak_match():
    invoice_line = {"sku": None, "description": "Office accessory"}
    po_lines = [
        {"po_line_id": "POL-1", "sku": "A", "description": "Business Laptop"},
        {"po_line_id": "POL-2", "sku": "B", "description": "LED Monitor"},
    ]
    selected, _ = _choose_po_line(invoice_line, po_lines)
    assert selected is None
