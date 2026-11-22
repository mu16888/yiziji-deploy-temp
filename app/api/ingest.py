"""Minimal ingestion endpoint: multipart upload -> extracted plain text.

This is the HTTP entry point for teacher-supplied material. The full generate-page
file-input wiring is future work; today a caller uploads here (or pastes text
directly into a job spec). All parsing/validation lives in ``app.services.ingest``.
"""

from __future__ import annotations

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from app.services.ingest import IngestError, extract_text

router = APIRouter(prefix="/api", tags=["ingest"])


@router.post("/ingest")
async def ingest(file: UploadFile = File(...)) -> dict:
    """Return ``{"text": ...}`` extracted from an uploaded lecture file."""
    data = await file.read()
    try:
        text = extract_text(file.filename or "", data)
    except IngestError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    return {"text": text}
