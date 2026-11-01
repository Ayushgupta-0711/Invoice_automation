"""OCR and text extraction for PDF and image invoices."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytesseract
from PIL import Image, ImageOps
from pypdf import PdfReader

from src.core.config import TESSDATA_PREFIX, TESSERACT_CMD

# Use the configured executable when present; otherwise allow Windows PATH lookup.
if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
MAX_PDF_PAGES = 20



def _tesseract_config() -> str:
    tessdata = Path(TESSDATA_PREFIX) if TESSDATA_PREFIX else None

    if tessdata and tessdata.is_dir():
        # Use forward slashes and no embedded quotes.
        # This avoids Windows argument-parsing issues in pytesseract.
        return f"--tessdata-dir {tessdata.as_posix()}"

    return ""


def _ocr_image(image: Image.Image) -> str:
    image = ImageOps.exif_transpose(image).convert("RGB")
    return pytesseract.image_to_string(
        image,
        lang="eng",
        config=_tesseract_config(),
    ).strip()


def extract_text_from_bytes(file_bytes: bytes, filename: str) -> str:
    """Extract digital PDF text or OCR text from a PDF/image upload.

    Scanned PDFs are rendered with PyMuPDF, so Poppler is not required.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported file type. Allowed extensions: {allowed}")
    if not file_bytes:
        raise ValueError("The uploaded file is empty.")

    if suffix == ".pdf":
        reader = PdfReader(BytesIO(file_bytes))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(f"PDF has more than {MAX_PDF_PAGES} pages. Split it before uploading.")
        embedded_text = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
        if len(embedded_text) >= 30:
            return embedded_text

        try:
            import pymupdf
        except ImportError as exc:  # pragma: no cover - environment-specific guidance
            raise RuntimeError(
                "Scanned PDF OCR requires PyMuPDF. Run: python -m pip install PyMuPDF"
            ) from exc

        extracted_pages: list[str] = []
        with pymupdf.open(stream=file_bytes, filetype="pdf") as document:
            if len(document) > MAX_PDF_PAGES:
                raise ValueError(f"PDF has more than {MAX_PDF_PAGES} pages. Split it before uploading.")
            for page in document:
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                extracted_pages.append(_ocr_image(image))
        return "\n\n".join(part for part in extracted_pages if part).strip()

    try:
        with Image.open(BytesIO(file_bytes)) as image:
            image.verify()
        with Image.open(BytesIO(file_bytes)) as image:
            return _ocr_image(image)
    except Exception as exc:
        raise ValueError(f"Could not read the uploaded image: {exc}") from exc


def extract_text(file_path: str | Path) -> str:
    """Extract text from a file already stored on disk."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Invoice file not found: {path}")
    return extract_text_from_bytes(path.read_bytes(), path.name)
