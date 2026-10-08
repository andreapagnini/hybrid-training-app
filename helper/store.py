"""Small key/value store for the helper's two files: the Garmin sign-in and
the record of workouts it created.

STORE_URL decides where they live:
  gs://bucket/prefix   Google Cloud Storage (production on Cloud Run)
  /some/local/folder   a folder on disk (running on a laptop, tests)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class Store:
    def get(self, name: str) -> str | None:
        raise NotImplementedError

    def put(self, name: str, text: str) -> None:
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError

    def get_json(self, name: str, default: Any) -> Any:
        raw = self.get(name)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except ValueError:
            return default

    def put_json(self, name: str, value: Any) -> None:
        self.put(name, json.dumps(value, separators=(",", ":"), sort_keys=True))


class LocalStore(Store):
    def __init__(self, folder: str) -> None:
        self.folder = Path(folder).expanduser()
        self.folder.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.folder / name

    def get(self, name: str) -> str | None:
        p = self._path(name)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def put(self, name: str, text: str) -> None:
        p = self._path(name)
        tmp = p.with_suffix(p.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        tmp.replace(p)

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)


class GcsStore(Store):
    def __init__(self, url: str) -> None:
        from google.cloud import storage  # imported here so local runs don't need it

        bucket, _, prefix = url[len("gs://"):].partition("/")
        self.bucket = storage.Client().bucket(bucket)
        self.prefix = prefix.strip("/")

    def _blob(self, name: str):
        return self.bucket.blob(f"{self.prefix}/{name}" if self.prefix else name)

    def get(self, name: str) -> str | None:
        blob = self._blob(name)
        return blob.download_as_text() if blob.exists() else None

    def put(self, name: str, text: str) -> None:
        self._blob(name).upload_from_string(text, content_type="application/json")

    def delete(self, name: str) -> None:
        blob = self._blob(name)
        if blob.exists():
            blob.delete()


def open_store(url: str) -> Store:
    return GcsStore(url) if url.startswith("gs://") else LocalStore(url)
