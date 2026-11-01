from decimal import Decimal

from src.services.workflow_service import MONEY_TOLERANCE, _money


def test_money_values_are_quantized_to_two_decimal_places():
    assert _money("12.345") == Decimal("12.35")
    assert _money("12") == Decimal("12.00")


def test_money_tolerance_is_one_paisa():
    assert MONEY_TOLERANCE == Decimal("0.01")


def test_workflow_module_exposes_all_expected_actions():
    from src import services
    from src.services import workflow_service

    for name in (
        "assign_exception",
        "resolve_exception",
        "request_approval",
        "decide_approval",
        "record_payment",
        "get_payment_summary",
    ):
        assert callable(getattr(workflow_service, name))


def test_fastapi_registers_exception_approval_and_payment_routes():
    from src.api.main import app

    paths = {route.path for route in app.routes}
    assert "/exceptions/{exception_id}/resolve" in paths
    assert "/invoices/{invoice_id}/approvals" in paths
    assert "/approvals/{approval_id}/decision" in paths
    assert "/invoices/{invoice_id}/payments" in paths
