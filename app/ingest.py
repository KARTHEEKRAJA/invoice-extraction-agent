"""Turn an uploaded file into something the model can read.

Two paths:
  1. Text path  - pdfplumber pulls the text layer out of a digital PDF.
                  Cheap, fast, and accurate when the PDF was generated
                  by software rather than scanned.
  2. Vision path - the document has no useful text layer (a scan or a
                  photo), so we render it to PNG and send the image.

Choosing between them automatically is what keeps the failure rate down.
Central AI does not pay for failed runs, so silently returning an empty
extraction from a scanned PDF would be a revenue bug as well as a
correctness one.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Literal

logger = logging.getLogger(__name__)

# Below this many characters we assume the text layer is junk or absent.
MIN_USABLE_TEXT = 120

# Cap pages sent to the vision model. Invoices are rarely long, and this
# bounds both cost and runtime.
MAX_VISION_PAGES = 3

SUPPORTED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}


class UnsupportedFileError(ValueError):
    """Raised when the upload is not a PDF or a supported image."""


def extract_pdf_text(data: bytes) -> str:
    """Pull the text layer from a PDF. Returns an empty string if there is none."""
    import pdfplumber

    chunks: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                chunks.append(text)
    return "\n\n".join(chunks).strip()


def render_pdf_to_images(data: bytes, max_pages: int = MAX_VISION_PAGES) -> list[str]:
    """Render PDF pages to base64 PNGs for the vision model."""
    import pypdfium2 as pdfium

    images: list[str] = []
    doc = pdfium.PdfDocument(io.BytesIO(data))
    try:
        for index in range(min(len(doc), max_pages)):
            page = doc[index]
            # scale=2 gives roughly 144 DPI, enough for small print on
            # invoices without inflating the payload.
            pil_image = page.render(scale=2).to_pil()
            buffer = io.BytesIO()
            pil_image.save(buffer, format="PNG")
            images.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
    finally:
        doc.close()
    return images


def prepare(
    data: bytes, content_type: str
) -> tuple[Literal["text", "vision"], str | list[str]]:
    """Decide how this document should be read.

    Returns a mode and the payload for that mode: either the extracted
    text, or a list of base64 PNG pages.
    """
    content_type = (content_type or "").lower().split(";")[0].strip()

    if content_type in SUPPORTED_IMAGE_TYPES:
        return "vision", [base64.b64encode(data).decode("ascii")]

    if content_type != "application/pdf":
        raise UnsupportedFileError(
            f"Unsupported file type {content_type!r}. Upload a PDF, PNG, or JPEG."
        )

    try:
        text = extract_pdf_text(data)
    except Exception:
        logger.warning("Text layer extraction failed, falling back to vision", exc_info=True)
        text = ""

    if len(text) >= MIN_USABLE_TEXT:
        return "text", text

    logger.info("Text layer too sparse (%d chars), using vision path", len(text))
    return "vision", render_pdf_to_images(data)
