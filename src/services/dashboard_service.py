"""Read-only queries used by the Streamlit dashboard."""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text
from database.connection import get_engine


def fetch_dataframe(sql: str, params: dict | None = None) -> pd.DataFrame:
    with get_engine().connect() as connection:
        return pd.read_sql(text(sql), connection, params=params or {})


def get_dashboard_metrics() -> dict[str, int | float]:
    sql = """
    SELECT
        (SELECT COUNT(*) FROM invoices) AS total_invoices,
        (SELECT COUNT(*) FROM invoices WHERE invoice_status = 'PENDING_APPROVAL') AS pending_approvals,
        (SELECT COUNT(*) FROM invoice_exceptions WHERE exception_status IN ('OPEN','IN_REVIEW') AND is_blocking = 1) AS open_blocking_exceptions,
        (SELECT COUNT(*) FROM invoices WHERE payment_ready = 1) AS payment_ready,
        (SELECT COALESCE(SUM(payment_amount), 0) FROM payments WHERE payment_status = 'PAID') AS total_paid
    """
    df = fetch_dataframe(sql)
    return df.iloc[0].to_dict()


def get_invoice_register(limit: int = 500) -> pd.DataFrame:
    return fetch_dataframe(
        """SELECT i.invoice_id, v.vendor_name, i.supplier_invoice_number,
                  i.po_id, i.invoice_date, i.currency_code,
                  i.calculated_total, i.invoice_status, i.match_status,
                  i.payment_ready
           FROM invoices i
           JOIN vendors v ON v.vendor_id = i.vendor_id
           ORDER BY i.received_at DESC
           LIMIT :limit""",
        {"limit": int(limit)},
    )


def get_open_exceptions(limit: int = 300) -> pd.DataFrame:
    return fetch_dataframe(
        """SELECT e.exception_id, e.invoice_id, v.vendor_name,
                  e.issue_type, e.severity, e.exception_status,
                  e.is_blocking, e.detected_at, e.description
           FROM invoice_exceptions e
           JOIN invoices i ON i.invoice_id = e.invoice_id
           JOIN vendors v ON v.vendor_id = i.vendor_id
           WHERE e.exception_status IN ('OPEN','IN_REVIEW')
           ORDER BY FIELD(e.severity, 'CRITICAL','HIGH','MEDIUM','LOW'), e.detected_at DESC
           LIMIT :limit""",
        {"limit": int(limit)},
    )
