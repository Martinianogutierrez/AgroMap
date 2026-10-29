from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


def _headers() -> dict[str, str]:
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return {"Authorization": f"Bearer {key}", "apikey": key}


def _base() -> str:
    return os.environ.get("SUPABASE_URL") or os.environ["NEXT_PUBLIC_SUPABASE_URL"]


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def download_object(
    bucket: str,
    path: str,
    dest: Path,
    *,
    retries: int = 4,
    backoff: float = 0.5,
    expected_sha256: str | None = None,
) -> Path:
    base = _base().rstrip("/")
    url = f"{base}/storage/v1/object/{bucket}/{path}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with httpx.Client(timeout=120.0, headers=_headers()) as client:
                r = client.get(url)
                r.raise_for_status()
                dest.write_bytes(r.content)
            if expected_sha256:
                got = sha256_file(dest)
                if got != expected_sha256:
                    raise RuntimeError(
                        f"checksum mismatch for {path}: "
                        f"got {got[:12]} expected {expected_sha256[:12]}"
                    )
            return dest
        except Exception as e:
            last_err = e
            log.warning("download attempt %s/%s failed: %s", attempt, retries, e)
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise RuntimeError(f"download failed after {retries} tries: {last_err}") from last_err


def upload_object(
    bucket: str,
    path: str,
    src: Path,
    content_type: str = "application/octet-stream",
    *,
    retries: int = 4,
    backoff: float = 0.5,
) -> str:
    """Upload and return sha256 of file bytes."""
    base = _base().rstrip("/")
    url = f"{base}/storage/v1/object/{bucket}/{path}"
    data = src.read_bytes()
    digest = sha256_bytes(data)
    headers = {
        **_headers(),
        "Content-Type": content_type,
        "x-upsert": "true",
        "x-checksum-sha256": digest,
    }
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with httpx.Client(timeout=300.0, headers=headers) as client:
                r = client.post(url, content=data)
                r.raise_for_status()
            return digest
        except Exception as e:
            last_err = e
            log.warning("upload attempt %s/%s failed: %s", attempt, retries, e)
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise RuntimeError(f"upload failed after {retries} tries: {last_err}") from last_err
