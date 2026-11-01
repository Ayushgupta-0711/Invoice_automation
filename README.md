# Invoice Automation System

A Python and MySQL application that demonstrates an invoice-processing and accounts-payable workflow. It covers invoice intake, OCR-assisted extraction, invoice validation, purchase-order and goods-receipt matching, exception handling, approval workflows, simulated payment recording, operational reporting and REST APIs.

> **Project status: Demonstration / MVP.** The application has been exercised locally with MySQL and a synthetic dataset. Payment recording creates accounting records in the application; it does **not** initiate or verify bank transfers. This project has not been represented as production-ready.

## Features

- **Invoice intake and OCR:** Upload PDF and image documents, extract text with Tesseract OCR and suggest invoice fields for human review.
- **Invoice validation:** Check invoice information, duplicate invoice numbers and arithmetic consistency across declared amounts, tax and calculated totals.
- **PO and goods-receipt matching:** Match invoice lines against purchase-order lines and accepted receipt quantities, with quantity, price and tax variance checks.
- **Exception management:** View, assign, review, resolve or waive exceptions with notes and workflow event history.
- **Approval workflow:** Submit eligible invoices for approval and record approve/reject decisions. The service checks invoice workflow prerequisites and approver eligibility.
- **Payment recording:** Record simulated full or partial payments for approved invoices, calculate outstanding balances and reject payment amounts that exceed the outstanding balance.
- **Operational dashboard:** Review invoices, exceptions, approvals and payments in Streamlit.
- **REST API:** Access invoice, matching, exception, approval and payment operations through FastAPI and interactive Swagger documentation.
- **Synthetic dataset:** Generate repeatable invoice scenarios with ground-truth labels for functional and matching evaluation.

## Technology stack

| Area | Technology |
|---|---|
| Language | Python 3.13 (tested locally) |
| Database | MySQL 8.0.44 |
| Database access | SQLAlchemy, PyMySQL |
| Dashboard | Streamlit |
| REST API | FastAPI, Uvicorn |
| OCR | Tesseract OCR, pytesseract, PyMuPDF for scanned-PDF rendering |
| Data processing | Pandas and supporting Python libraries |
| Testing | pytest |

## High-level workflow

1. A user uploads an invoice document.
2. The application extracts embedded PDF text where available and uses OCR for images or scanned PDFs.
3. Extracted fields are presented for human review and correction before the invoice is saved.
4. The application validates invoice details, duplicate information and amount calculations.
5. PO-backed invoices are matched against purchase-order lines and posted goods receipts. Non-PO invoices require account coding.
6. Variances and policy issues are recorded as exceptions for review.
7. Eligible invoices can be submitted to an active approver and decided through the approval workflow.
8. After required approvals and workflow checks, a simulated payment can be recorded. Payment and invoice event records preserve the workflow history.

Resolving an exception changes its workflow status; it should not be treated as proof that the underlying business discrepancy has been corrected. Users should verify supporting documents and invoice data before proceeding.

## Invoice intake and OCR

The Streamlit **Invoice Intake** page accepts PDF and image files (PNG, JPG/JPEG, TIFF and BMP) up to 15 MB. The application extracts embedded PDF text where available and runs Tesseract OCR for images and scanned PDFs. Scanned PDF pages are rendered with PyMuPDF; Poppler is not required by this implementation.

OCR output is a suggestion, not an authoritative record. A user should review the vendor, supplier invoice number, dates, PO selection, declared amounts and line items before saving. Non-PO invoices require manual account coding; the workflow checks that the gross coding allocation reconciles to the calculated invoice total.

The intake implementation stores source documents under `data/raw/YYYY/MM/` using a SHA-256-based filename and stores the relative path and document hash with the invoice record. Review the `data/raw/` contents and `.gitignore` before publishing; do not upload confidential source invoices.

OCR quality depends on scan resolution, orientation, print quality and document layout. Always compare extracted values with the original document before saving.

## PO and goods-receipt matching

The **Matching Check** page provides an automatic matching action. The matching implementation:

- Tries an exact SKU match first, then uses a conservative description-similarity threshold.
- Uses posted goods receipts and accepted quantities.
- Reserves quantities already allocated to other invoices so the same PO/receipt quantity cannot be billed twice.
- Checks quantity availability, unit-price variance (default 1%), tax-rate variance (default 0.01 percentage points), duplicate submissions, vendor status and invoice-header arithmetic.
- Writes match allocations, blocking invoice exceptions and an invoice event within a database transaction.
- Does not approve invoices or authorize payments.

Description similarity is a first-pass heuristic. Ambiguous matches should be reviewed by a user. Production use may require vendor-specific SKU aliases, richer item normalization and explicit human confirmation.

## Exception, approval and payment workflows

### Exceptions

The **Exceptions** page supports assignment, moving an exception to `IN_REVIEW`, and resolving or waiving it with notes. Unresolved blocking exceptions prevent downstream approval/payment gates. Resolved historical exceptions may retain their original `is_blocking` classification; workflow logic must also check the exception status when determining whether a blocker is still active.

### Approvals

The **Approvals** page supports approval requests and approve/reject decisions. Only eligible invoices should be offered for submission. The workflow checks active vendor status, unresolved blocking exceptions, completed PO matching or non-PO account coding, and approver eligibility. The service also prevents rejected or otherwise ineligible invoice statuses from being submitted again.

### Payments

The **Payments** page displays paid and outstanding balances and can record a payment as completed in the demonstration system. Payments are accounting records only: the application does not connect to a bank, initiate a transfer or independently confirm funds. Payment recording requires an eligible invoice, an approved approval record, completed approval levels, an active processor and a positive amount that does not exceed the outstanding balance. Partial payments are supported.

The payment service records payment events and an invoice event, then updates the invoice status and outstanding balance. For example, a partial payment leaves the invoice in `PARTIALLY_PAID`; a fully settled invoice moves to `PAID`.

## Database overview

The local MySQL database is named `invoice_automation`. The schema contains 17 core tables:

- `vendors`
- `users`
- `tax_codes`
- `purchase_orders`
- `purchase_order_lines`
- `goods_receipts`
- `goods_receipt_lines`
- `invoices`
- `invoice_lines`
- `invoice_po_line_matches`
- `invoice_account_codings`
- `approvals`
- `payments`
- `invoice_exceptions`
- `invoice_events`
- `approval_events`
- `payment_events`

Relational keys and constraints connect vendors, purchase orders, receipts, invoices, line matches, approvals, exceptions and payments. Follow the schema/setup instructions included in the repository when creating a fresh database. `database/verify_schema.sql` is for schema verification; do not assume that it creates all tables or reference data.

## Project structure

```text
Invoice_automation/
├── config/                 # Application configuration
├── dashboard/              # Streamlit application and UI modules
├── database/               # Database connection and SQL scripts
├── data/                   # Dataset generator and local data
├── docs/                   # Project documentation
├── sample_data/            # Sample invoice inputs (review before publishing)
├── src/
│   ├── api/                # FastAPI application and routes
│   ├── ocr/                # OCR engine and extraction support
│   └── services/           # Validation, matching and workflow services
├── tests/                  # Automated tests
├── check_ocr.py            # Tesseract readiness check
├── requirements.txt
├── run_api.bat
├── run_dashboard.bat
└── README.md
```

The exact contents may evolve as the project is updated.

## Prerequisites

- Windows 10/11 or another supported Python environment.
- Python 3.13 recommended (tested locally with Python 3.13.0).
- MySQL Server 8.0 or compatible.
- Tesseract OCR installed, with its executable and language-data directory accessible.
- Git, if cloning or pushing the repository.

The local OCR setup used English language data (`eng`) and orientation/script detection (`osd`). Confirm that the required Tesseract language files are installed on your machine.

## Local setup (Windows CMD)

### 1. Clone the repository

```cmd
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd Invoice_automation
```

Replace the placeholder with your GitHub repository URL. If the cloned folder has a different name, change into that folder instead.

### 2. Create and activate a virtual environment

```cmd
py -3.13 -m venv venv
venv\Scripts\activate
```

If you already have a suitable virtual environment, activate it instead of creating another one.

### 3. Install dependencies

```cmd
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy the example environment file:

```cmd
copy .env.example .env
```

Edit `.env` with your local MySQL credentials and Tesseract paths. For example:

```dotenv
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=invoice_automation
DB_USER=your_mysql_user
DB_PASSWORD=your_mysql_password
TESSERACT_CMD=C:/Path/To/tesseract.exe
TESSDATA_PREFIX=C:/Path/To/tessdata
```

Use paths that exist on your computer. Keep `.env` private and never commit real credentials to GitHub. `.env.example` should contain placeholders only.

### 5. Create and initialize the database

1. Start MySQL Server.
2. Create a database named `invoice_automation` (or update `DB_NAME` to your chosen database).
3. Follow the schema-creation and reference-data setup instructions included in `docs/` and the SQL files supplied in the repository.
4. Ensure required reference data, such as users and tax codes, exists before running workflows that depend on it.
5. Verify the database connection using the project's database-check utility or the dashboard's **Test database connection** control.

For example, the database container can be created with:

```sql
CREATE DATABASE invoice_automation;
```

This command creates only the database; it does not create the application tables or seed the required reference data.

## Run the application

Open a CMD terminal in the repository root and activate the virtual environment.

### Streamlit dashboard

```cmd
streamlit run dashboard\app.py
```

### FastAPI backend

Open another activated CMD window in the project root:

```cmd
python -m uvicorn src.api.main:app --reload
```

When the API is running locally, open:

- Swagger UI: <http://127.0.0.1:8000/docs>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>
- Health check: <http://127.0.0.1:8000/health>

The dashboard and API are separate processes. Start each in its own terminal when using both at the same time.

## API endpoints

The local Swagger documentation exposes the following operations. Use `/docs` to inspect current request schemas and required fields.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Check application and MySQL connectivity |
| GET | `/invoices` | List invoices (supports a `limit` query parameter) |
| GET | `/invoices/{invoice_id}` | Retrieve invoice details |
| POST | `/invoices/{invoice_id}/validate` | Validate invoice details and amounts |
| POST | `/invoices/{invoice_id}/match` | Run invoice-to-PO/receipt matching |
| GET | `/invoices/{invoice_id}/matching` | Retrieve matching allocations/results |
| GET | `/exceptions` | List invoice exceptions |
| POST | `/exceptions/{exception_id}/assign` | Assign an exception |
| POST | `/exceptions/{exception_id}/resolve` | Resolve an exception |
| POST | `/invoices/{invoice_id}/approvals` | Request invoice approval |
| POST | `/approvals/{approval_id}/decision` | Record an approval decision |
| POST | `/invoices/{invoice_id}/payments` | Record a simulated payment |

GET endpoints listed above are read-oriented. POST endpoints can change database records; use a test database and test records when experimenting.

## Synthetic dataset generator

The project includes a deterministic generator for a relational accounts-payable dataset. It writes CSV files and validation metadata by default; it does **not** change MySQL unless the explicit `--load-mysql` option is supplied.

### Default dataset profile

| Entity | Generated rows |
|---|---:|
| Vendors | 300 |
| Purchase orders | 1,500 |
| Purchase order lines | 2,200 |
| Goods receipt headers | 1,800 |
| Goods receipt lines | 2,633 in the tested seed |
| Invoices | 2,000 |
| Invoice lines | 2,000 |
| Invoice-to-PO/receipt allocations | 1,610 in the tested seed |
| Non-PO account codings | 300 |
| Approvals | 1,400 |
| Payments | 193 |
| Invoice exceptions | 600 |
| Invoice events | 2,000 |
| Approval events | 2,357 |
| Payment events | 579 |

The seed `20261022` produced 2,000 invoice ground-truth rows with the following mutually exclusive primary scenarios:

| Scenario | Invoices | Share |
|---|---:|---:|
| Clean PO-backed | 1,100 | 55% |
| Clean non-PO | 300 | 15% |
| Price variance | 200 | 10% |
| Quantity mismatch | 160 | 8% |
| Duplicate submission | 100 | 5% |
| Missing receipt | 80 | 4% |
| Tax mismatch | 30 | 1.5% |
| Blocked vendor | 30 | 1.5% |
| **Total** | **2,000** | **100%** |

`ground_truth.csv` is separate from transaction data. Do not use its labels as model input features; use them only for evaluation to avoid target leakage.

### Generate CSV data

Run from the repository root with the virtual environment active:

```cmd
python data\generator\generate_dataset.py --seed 20261022
```

By default, files are written to:

```text
data\generated\seed_20261022\
```

The output folder contains CSV files for the generated tables, `ground_truth.csv`, `manifest.json` and `validation_report.json`. The manifest records SHA-256 checksums. Running the same seed into a different empty output folder should produce identical generated CSV checksums.

To generate into a separate folder for a reproducibility comparison:

```cmd
python data\generator\generate_dataset.py --seed 20261022 --out data\generated\repro_check
```

The generator stops if the chosen output directory already contains files. Use `--overwrite` only when you intentionally want to replace the generated output in that directory. Prefer a new output folder when preserving a prior dataset.

### Optional MySQL import

The default command only creates files. To append a generated dataset to MySQL, first take a database backup, verify the target database, and ensure the required reference data exists. From Windows CMD at the project root, set `PYTHONPATH` so the generator can import the root-level `database` package, then run the generator with a **new, empty output directory**:

```cmd
set PYTHONPATH=%CD%
python data\generator\generate_dataset.py --seed 20261022 --out data\generated\seed_20261022_import --load-mysql
```

The loader uses the project's `.env` connection settings and inserts tables in foreign-key dependency order inside one transaction. If an insert fails, the transaction rolls back. The loader does not create duplicate users or tax codes; it expects these master records to exist:

- Users: `USR-00001`, `USR-00002`, `USR-00003`
- Tax codes: `GST_0`, `GST_5`, `GST_12`, `GST_18`, `GST_28`

Generated entity IDs use the `SYN-` prefix and generated vendor codes use the `SYNV` prefix. The loader preflights primary-key collisions and aborts if generated IDs already exist. Do not run the same seed import again against a database that already contains those generated records. The local development run imported 2,000 synthetic invoices while retaining the existing non-synthetic demonstration records.

### Dataset validation

Before using a generated dataset, inspect `validation_report.json` and confirm it contains:

```json
{"passed": true}
```

The generator validates primary-key uniqueness, parent/child references, PO/vendor consistency, receipt quantity arithmetic, invoice arithmetic, payment-to-approval consistency and scenario counts. The CSV generator and validation report are reproducibility/evaluation utilities; they do not independently establish OCR accuracy or production performance.

## Tests and local verification

Run the automated test suite from the repository root:

```cmd
python -m pytest -q
```

The latest local test run reported **15 passed**. The following checks were also performed during development:

- Python compilation checks for modified dashboard and workflow modules.
- Tesseract readiness check:

  ```cmd
  python check_ocr.py
  ```

  The local check detected Tesseract `5.5.3.20260724` and the `eng` and `osd` language packs. This verifies OCR engine availability, not extraction accuracy.
- FastAPI `GET /health` returned HTTP 200 and confirmed MySQL connectivity.
- Invoice listing, invoice detail and invoice matching GET endpoints returned HTTP 200 in Swagger UI.
- The exceptions listing GET endpoint was also exercised successfully.
- A partial-payment test recorded ₹1,000 against a ₹4,130 synthetic invoice, leaving ₹3,130 outstanding.
- The payment service validates positive amounts, supported payment methods, invoice/approval eligibility, active processors and amounts that do not exceed the outstanding balance.

These are local development verification results, not a claim of exhaustive test coverage, production load testing or independent OCR accuracy measurement.

## Security, limitations and before publishing

- **No real bank integration:** Payments are demonstration records only. No banking network, payment gateway or funds transfer is connected.
- **Synthetic data:** Generated records are for development and evaluation, not real vendor or customer data.
- **OCR review required:** OCR can misread text. Users must review extracted values against the source document.
- **Authentication and authorization:** The local development screens and API do not provide a complete production authentication and role-based access-control system. Production use requires authenticated identities and server-side authorization checks.
- **Production hardening:** Before production use, add and validate secrets management, audit controls, deployment hardening, backups/recovery, monitoring, privacy controls, concurrency handling and broader integration/security testing.
- **Exception resolution:** A resolved exception status is not, by itself, proof that the underlying discrepancy was corrected. Review source evidence before approval.
- **Do not publish private files:** Keep `.env`, database dumps/backups, `venv/`, logs, caches, generated local data and machine-specific files out of the public repository unless intentionally reviewed and sanitized.
- **Review sample documents:** Inspect `sample_data/` and `data/raw/` to ensure they contain no personal, confidential or licensed information before publishing.

Before committing, inspect the files Git will include:

```cmd
git status --short
```

The repository `.gitignore` should exclude at least:

```gitignore
.env
venv/
__pycache__/
.pytest_cache/
*.py[cod]
*.log
logs/
backup_*.sql
*.sql.bak
data/raw/
data/generated/
```

Keep `.env.example` in the repository, with placeholders only. Review all staged files before pushing.

## License

No license is specified in this repository yet. Add a license file before allowing others to reuse, modify or redistribute the project.
