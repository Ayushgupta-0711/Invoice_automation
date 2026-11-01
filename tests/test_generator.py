from data.generator.generate_dataset import generate, validate


def test_default_synthetic_dataset_has_expected_counts_and_scenarios():
    tables, ground_truth = generate(seed=20261022)
    report = validate(tables, ground_truth)

    assert report["passed"], report["errors"]
    assert len(tables["vendors"]) == 300
    assert len(tables["purchase_orders"]) == 1500
    assert len(tables["purchase_order_lines"]) == 2200
    assert len(tables["goods_receipts"]) == 1800
    assert len(tables["invoices"]) == 2000
    assert len(tables["invoice_lines"]) == 2000
    assert report["invoice_scenario_counts"] == {
        "CLEAN_PO": 1100,
        "CLEAN_NON_PO": 300,
        "PRICE_VARIANCE": 200,
        "QUANTITY_MISMATCH": 160,
        "MISSING_RECEIPT": 80,
        "TAX_MISMATCH": 30,
        "VENDOR_BLOCKED": 30,
        "DUPLICATE": 100,
    }


def test_duplicate_records_link_to_the_original_and_have_a_duplicate_exception():
    tables, ground_truth = generate(seed=20261022)
    invoices = {row["invoice_id"]: row for row in tables["invoices"]}
    duplicate_labels = [row for row in ground_truth if row["primary_scenario"] == "DUPLICATE"]
    assert len(duplicate_labels) == 100
    for label in duplicate_labels:
        duplicate = invoices[label["invoice_id"]]
        assert duplicate["original_invoice_id"] in invoices
        original = invoices[duplicate["original_invoice_id"]]
        assert duplicate["vendor_id"] == original["vendor_id"]
        assert duplicate["supplier_invoice_number"] == original["supplier_invoice_number"]
