"""POST /api/ingest: the minimal upload API that wires ``extract_text`` into the app.

A teacher (or a future generate-page UI) uploads a whitelisted file via
multipart and receives the extracted plain text. Disallowed extensions are
rejected with 400. No auth/key/network is involved.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_ingest_markdown_upload_returns_text():
    r = client.post(
        "/api/ingest",
        files={"file": ("notes.md", "# 讲义\n一元二次方程", "text/markdown")},
    )
    assert r.status_code == 200
    assert r.json() == {"text": "# 讲义\n一元二次方程"}


def test_ingest_plain_text_upload_is_case_insensitive():
    r = client.post(
        "/api/ingest",
        files={"file": ("LECTURE.TXT", b"hello classroom", "text/plain")},
    )
    assert r.status_code == 200
    assert r.json() == {"text": "hello classroom"}


def test_ingest_rejects_non_whitelisted_extension():
    r = client.post(
        "/api/ingest",
        files={"file": ("evil.exe", b"MZ...", "application/octet-stream")},
    )
    assert r.status_code == 400
    assert "extension" in r.json()["detail"]
