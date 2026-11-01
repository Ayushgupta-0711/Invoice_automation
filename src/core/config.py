"""Application settings."""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

APP_NAME = os.getenv("APP_NAME", "Invoice Automation System")
APP_ENV = os.getenv("APP_ENV", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
TESSERACT_CMD = os.getenv("TESSERACT_CMD", r"E:\Tesseract\tesseract.exe")
TESSDATA_PREFIX = os.getenv("TESSDATA_PREFIX", r"E:\Tesseract\tessdata")
