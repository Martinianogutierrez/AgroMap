from __future__ import annotations

import os
from pathlib import Path

import httpx


def _headers() -> dict[str, str]:
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return {"Authorization": f"Bearer {key}", "apikey": key}


def download_object(bucket: str, path: str, dest: Path) -> Path:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    url = f"{base}/storage/v1/object/{bucket}/{path}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120.0, headers=_headers()) as client:
        r = client.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)
    return dest


def upload_object(
    bucket: str,
    path: str,
    src: Path,
    content_type: str = "application/octet-stream",
) -> None:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    url = f"{base}/storage/v1/object/{bucket}/{path}"
    headers = {**_headers(), "Content-Type": content_type, "x-upsert": "true"}
    with httpx.Client(timeout=300.0, headers=headers) as client:
        r = client.post(url, content=src.read_bytes())
        r.raise_for_status()
