from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from worker import storage


def test_sha256_file(tmp_path: Path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"abc")
    h = storage.sha256_file(p)
    assert len(h) == 64
    assert h == storage.sha256_file(p)


def test_download_retries_then_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SUPABASE_URL", "http://example.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    dest = tmp_path / "out.bin"
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, ok: bool, content: bytes = b"data"):
            self._ok = ok
            self.content = content
            self.status_code = 200 if ok else 500

        def raise_for_status(self) -> None:
            if not self._ok:
                raise httpx.HTTPStatusError(
                    "err", request=MagicMock(), response=MagicMock(status_code=500)
                )

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url: str):
            calls["n"] += 1
            if calls["n"] < 3:
                return FakeResp(False)
            return FakeResp(True, b"ok-bytes")

        def post(self, url: str, content: bytes = b""):
            return FakeResp(True)

    with patch("worker.storage.httpx.Client", FakeClient):
        storage.download_object("uploads", "owner/f.bin", dest, retries=3, backoff=0.01)
    assert dest.read_bytes() == b"ok-bytes"
    assert calls["n"] == 3


def test_upload_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SUPABASE_URL", "http://example.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    src = tmp_path / "in.bin"
    src.write_bytes(b"xyz")
    calls = {"n": 0}

    class FakeResp:
        def __init__(self, ok: bool):
            self._ok = ok
            self.status_code = 200 if ok else 503

        def raise_for_status(self) -> None:
            if not self._ok:
                raise httpx.HTTPStatusError(
                    "err", request=MagicMock(), response=MagicMock(status_code=503)
                )

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url: str, content: bytes = b""):
            calls["n"] += 1
            if calls["n"] < 2:
                return FakeResp(False)
            return FakeResp(True)

    with patch("worker.storage.httpx.Client", FakeClient):
        storage.upload_object("outputs", "o/x.bin", src, retries=3, backoff=0.01)
    assert calls["n"] == 2
