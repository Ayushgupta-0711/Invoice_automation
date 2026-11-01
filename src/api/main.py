"""FastAPI application for Invoice Automation."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database.connection import get_engine, test_connection
from src.core.config import APP_NAME
from src.services.invoice_service import get_invoice, validate_invoice
from src.matching.matching_service import inspect_invoice_matches, run_three_way_match
from src.services.workflow_service import (
    assign_exception, resolve_exception, request_approval,
    decide_approval, record_payment,
)

app = FastAPI(title=APP_NAME, version="0.2.0", description="Invoice processing and accounts-payable workflow API")


class AssignExceptionRequest(BaseModel):
    assigned_to: str = Field(min_length=1, max_length=30)


class ResolveExceptionRequest(BaseModel):
    resolved_by: str = Field(min_length=1, max_length=30)
    resolution_notes: str = Field(min_length=8, max_length=4000)
    waive: bool = False


class ApprovalRequest(BaseModel):
    approver_id: str = Field(min_length=1, max_length=30)
    comments: Optional[str] = Field(default=None, max_length=2000)


class ApprovalDecisionRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=30)
    decision: str = Field(pattern="^(APPROVED|REJECTED)$")
    comments: Optional[str] = Field(default="", max_length=2000)


class PaymentRequest(BaseModel):
    approval_id: str = Field(min_length=1, max_length=50)
    processed_by: str = Field(min_length=1, max_length=30)
    payment_amount: Decimal = Field(gt=0)
    payment_method: str = Field(min_length=2, max_length=30)
    bank_reference: Optional[str] = Field(default=None, max_length=100)


@app.get("/health")
def health() -> dict:
    try:
        return {"application": "healthy", "database": test_connection()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Database connection failed: {exc}") from exc


@app.get("/invoices")
def list_invoices(limit: int = 100) -> list[dict]:
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 500")
    sql = text("""SELECT invoice_id, vendor_id, po_id, supplier_invoice_number,
                         invoice_date, calculated_total, invoice_status,
                         match_status, payment_ready
                  FROM invoices ORDER BY received_at DESC LIMIT :limit""")
    with get_engine().connect() as conn:
        return [dict(row) for row in conn.execute(sql, {"limit": limit}).mappings().all()]


@app.get("/invoices/{invoice_id}")
def invoice_detail(invoice_id: str) -> dict:
    invoice = get_invoice(invoice_id)
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@app.post("/invoices/{invoice_id}/validate")
def invoice_validate(invoice_id: str) -> dict:
    try:
        return validate_invoice(invoice_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Validation failed: {exc}") from exc


@app.post("/invoices/{invoice_id}/match")
def invoice_match(invoice_id: str) -> dict:
    try:
        return run_three_way_match(invoice_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Matching failed: {exc}") from exc


@app.get("/invoices/{invoice_id}/matching")
def invoice_matching(invoice_id: str) -> dict:
    try:
        return inspect_invoice_matches(invoice_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/exceptions")
def list_exceptions(open_only: bool = True, limit: int = 300) -> list[dict]:
    if not 1 <= limit <= 1000:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 1000")
    where = "WHERE e.exception_status IN ('OPEN','IN_REVIEW')" if open_only else ""
    sql = text(f"""SELECT e.exception_id, e.invoice_id, e.issue_type, e.severity,
            e.exception_status, e.is_blocking, e.assigned_to, e.description,
            e.detected_at, e.resolved_at, e.resolution_notes
        FROM invoice_exceptions e {where}
        ORDER BY e.detected_at DESC LIMIT :limit""")
    with get_engine().connect() as conn:
        return [dict(row) for row in conn.execute(sql, {"limit": limit}).mappings().all()]


@app.post("/exceptions/{exception_id}/assign")
def exception_assign(exception_id: str, payload: AssignExceptionRequest) -> dict:
    try:
        return assign_exception(exception_id, payload.assigned_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/exceptions/{exception_id}/resolve")
def exception_resolve(exception_id: str, payload: ResolveExceptionRequest) -> dict:
    try:
        return resolve_exception(exception_id, payload.resolved_by, payload.resolution_notes, waive=payload.waive)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/invoices/{invoice_id}/approvals")
def approval_request(invoice_id: str, payload: ApprovalRequest) -> dict:
    try:
        return request_approval(invoice_id, payload.approver_id, payload.comments)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/approvals/{approval_id}/decision")
def approval_decision(approval_id: str, payload: ApprovalDecisionRequest) -> dict:
    try:
        return decide_approval(approval_id, payload.actor_id, payload.decision, payload.comments or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/invoices/{invoice_id}/payments")
def payment_record(invoice_id: str, payload: PaymentRequest) -> dict:
    try:
        return record_payment(
            invoice_id, payload.approval_id, payload.processed_by,
            payload.payment_amount, payload.payment_method, payload.bank_reference,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
