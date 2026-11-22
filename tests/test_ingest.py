"""Document ingestion: extension whitelist, size cap, format parsing and byte hygiene."""

from io import BytesIO

import pytest

from app.services.ingest import (
    SUPPORTED_EXT,
    IngestError,
    extract_text,
    looks_supported,
)


# --- test fixture builders (stdlib + python-docx, no heavy PDF dependency) ----


def _make_pdf(text: str) -> bytes:
    """Handcraft a minimal, structurally valid single-page PDF containing ``text``."""
    content = ("BT /F1 12 Tf 72 720 Td (" + text + ") Tj ET").encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n"
        + content
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
    out += f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    return bytes(out)


def _make_docx(*paragraphs: str) -> bytes:
    from docx import Document

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    stream = BytesIO()
    doc.save(stream)
    return stream.getvalue()


# --- contract ----------------------------------------------------------------


def test_public_interface():
    assert SUPPORTED_EXT == {".txt", ".md", ".markdown", ".pdf", ".docx"}
    assert issubclass(IngestError, Exception)


# --- text / markdown (brief Step 1 body verbatim) -----------------------------


def test_extract_plain_text():
    assert extract_text("a.md", b"# Hello") == "# Hello"


def test_extract_text_formats_and_case_insensitive_extension():
    data = b"line one\nline two"
    for name in ("a.txt", "a.md", "a.markdown", "A.TXT", "notes.MD"):
        assert extract_text(name, data) == data.decode("utf-8")


def test_extract_text_unicode_and_invalid_bytes_replaced():
    assert extract_text("u.txt", "café 课堂".encode("utf-8")) == "café 课堂"
    # Invalid UTF-8 is decoded with errors="replace", never raised as a parse error.
    decoded = extract_text("bad.txt", b"caf\xe9")
    assert decoded.startswith("caf")


# --- pdf / docx ---------------------------------------------------------------


def test_extract_pdf():
    pdf = _make_pdf("Hello PDF")
    assert "Hello PDF" in extract_text("paper.pdf", pdf)


def test_extract_docx():
    docx = _make_docx("第一章", "课堂互动设计")
    text = extract_text("lesson.docx", docx)
    assert "第一章" in text
    assert "课堂互动设计" in text


# --- review focus: malicious / invalid files ----------------------------------


def test_ingest_rejects_bad_file():
    with pytest.raises(IngestError):
        extract_text("evil.exe", b"MZ...")
    with pytest.raises(IngestError):
        extract_text("big.txt", b"x" * (11 * 1024 * 1024))


def test_no_extension_rejected():
    with pytest.raises(IngestError):
        extract_text("README", b"content")


def test_parse_failure_does_not_leak_file_bytes():
    marker = b"TOPSECRETFILEBYTES_DO_NOT_LEAK"
    for name in ("broken.pdf", "broken.docx"):
        with pytest.raises(IngestError) as exc:
            extract_text(name, marker)
        assert "TOPSECRETFILEBYTES_DO_NOT_LEAK" not in str(exc.value)


# --- size cap / looks_supported ------------------------------------------------


def test_custom_max_bytes_boundary():
    assert extract_text("a.txt", b"12345", max_bytes=5) == "12345"
    with pytest.raises(IngestError):
        extract_text("a.txt", b"123456", max_bytes=5)


def test_looks_supported():
    for name in ("a.txt", "a.md", "a.markdown", "a.pdf", "a.docx", "A.PDF"):
        assert looks_supported(name) is True
    for name in ("a.exe", "README", "a.doc", "a.exe.txt.md.exe"):
        assert looks_supported(name) is False
