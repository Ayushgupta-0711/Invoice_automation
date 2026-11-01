
import pytesseract
from PIL import Image

# Tesseract installation path
pytesseract.pytesseract.tesseract_cmd = (
    r"E:\Tesseract\tesseract.exe"
)

# Verify OCR engine
print("Tesseract:", pytesseract.get_tesseract_version())

# Verify available languages
print("Available languages:", pytesseract.get_languages(config=""))

print("\nTesseract OCR is ready!")
