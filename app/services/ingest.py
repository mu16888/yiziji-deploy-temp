"""Extract plain text from teacher-supplied material files.

Pure service: accepts raw bytes (already read by the caller) and returns text for
the generation orchestrator. Only the stdlib plus ``pypdf`` / ``python-docx`` are
used; no FastAPI and no network access. Extension whitelisting and the size cap
keep hostile or accidental uploads from reaching the parsers.
"""

from __future__ import annotations

import os
from io import BytesIO

SUPPORTED_EXT = {".txt", ".md", ".markdown", ".pdf", ".docx"}
_TEXT_EXT = {".txt", ".md", ".markdown"}


class IngestError(Exception):
    """Raised when a file cannot be safely ingested (bad type/size or parse failure)."""


def _extension(filename: str) -> str:
    """Return the lower-cased extension of ``filename`` (empty string if none)."""
    return os.path.splitext(filename)[1].lower()


def looks_supported(filename: str) -> bool:
    """True when ``filename`` carries a whitelisted, case-insensitive extension.

    Use this for cheap front-end / upload pre-validation; it inspects the name
    only and never touches file bytes.
    """
    return _extension(filename) in SUPPORTED_EXT


def extract_text(
    filename: str, data: bytes, *, max_bytes: int = 10 * 1024 * 1024
) -> str:
    """Decode ``data`` to plain text according to ``filename``'s extension.

    Rejects non-whitelisted extensions and data larger than ``max_bytes`` with
    ``IngestError``. Parser failures are also reported as ``IngestError`` using
    static messages so no raw file bytes can leak into the error text.
    """
    ext = _extension(filename)
    if ext not in SUPPORTED_EXT:
        raise IngestError("unsupported file extension")
    if len(data) > max_bytes:
        raise IngestError("file exceeds maximum allowed size")

    if ext in _TEXT_EXT:
        return data.decode("utf-8", errors="replace")

    if ext == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(data))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            # Static message: never echo the exception or the file bytes.
            raise IngestError("failed to parse PDF document") from None

    # ext == ".docx"
    try:
        from docx import Document

        document = Document(BytesIO(data))
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    except Exception:
        raise IngestError("failed to parse Word document") from None
