"""The Garmin sign-in: connect (with two-step code), resume, disconnect.

The password is used once to sign in and is never stored. What is stored is
the sign-in Garmin hands back (its access and refresh tokens), kept in the
helper's store as garmin_tokens.json. The toolkit refreshes it on its own and
writes the new one to a private temporary folder; persist() copies any change
back to the store.
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from store import Store

TOKENS = "garmin_tokens.json"
MFA_TIMEOUT_SEC = 600


def _default_factory(**kwargs: Any):
    from garminconnect import Garmin

    return Garmin(**kwargs)


class NotConnected(Exception):
    pass


class GarminAccount:
    def __init__(self, store: Store, factory: Callable[..., Any] = _default_factory) -> None:
        self.store = store
        self.factory = factory
        self.lock = threading.RLock()
        self._dir = Path(tempfile.mkdtemp(prefix="garmin-"))
        self._dir.chmod(0o700)
        self._client: Any = None
        self._pending: tuple[Any, float] | None = None
        self._saved: str | None = None

    @property
    def _token_file(self) -> Path:
        return self._dir / TOKENS

    # -- state ---------------------------------------------------------------
    def connected(self) -> bool:
        return self.store.get(TOKENS) is not None

    def display_name(self) -> str | None:
        c = self._client
        return getattr(c, "full_name", None) or getattr(c, "display_name", None) if c else None

    def client(self) -> Any:
        """A signed-in client, restored from the stored sign-in if needed."""
        with self.lock:
            if self._client is not None:
                return self._client
            saved = self.store.get(TOKENS)
            if saved is None:
                raise NotConnected("Garmin is not connected")
            self._token_file.write_text(saved, encoding="utf-8")
            self._token_file.chmod(0o600)
            c = self.factory()
            c.login(str(self._dir))
            self._client, self._saved = c, saved
            self.persist()
            return c

    def persist(self) -> None:
        """Copy a refreshed sign-in back to the store."""
        with self.lock:
            if not self._token_file.exists():
                return
            now = self._token_file.read_text(encoding="utf-8")
            if now and now != self._saved:
                self.store.put(TOKENS, now)
                self._saved = now

    def _adopt(self, c: Any) -> None:
        c.client.dump(str(self._dir))
        c.client._tokenstore_path = str(self._dir)
        self._client, self._saved = c, None
        self.persist()

    # -- connect -------------------------------------------------------------
    def start_login(self, email: str, password: str) -> str:
        """Returns "connected", or "mfa" when Garmin wants the two-step code."""
        with self.lock:
            self._client = None
            c = self.factory(email=email, password=password, return_on_mfa=True)
            status, _ = c.login()
            if status == "needs_mfa":
                self._pending = (c, time.monotonic() + MFA_TIMEOUT_SEC)
                return "mfa"
            self._pending = None
            self._adopt(c)
            return "connected"

    def finish_mfa(self, code: str) -> None:
        with self.lock:
            if not self._pending or self._pending[1] < time.monotonic():
                self._pending = None
                raise NotConnected("The sign-in expired. Start again with your email and password.")
            c = self._pending[0]
            c.resume_login(None, code)
            self._pending = None
            self._adopt(c)

    def import_tokens(self, tokens: str) -> None:
        """Fallback: a sign-in made on a laptop with login_locally.py."""
        with self.lock:
            self.store.put(TOKENS, tokens)
            self._client = None
            self._saved = None
            self.client()

    def disconnect(self) -> None:
        with self.lock:
            self.store.delete(TOKENS)
            self._token_file.unlink(missing_ok=True)
            self._client = None
            self._pending = None
            self._saved = None

    def forget_client(self) -> None:
        """Drop the in-memory client so the next call restores it again."""
        with self.lock:
            self._client = None
