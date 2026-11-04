"""Persistent HOTP counter state.

The counter is the mutable shared state that makes HOTP un-syncable. This
module makes it safe for concurrent readers/writers on one host: every
read-modify-write runs under an exclusive ``flock``, and the file is replaced
atomically so a crash never leaves a half-written state.

Only counters live here. Secrets stay in the config file.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

__all__ = ["StateError", "StateStore"]


class StateError(RuntimeError):
    """Raised when the state file cannot be read or written."""


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class StateStore:
    """A JSON file holding ``{token_name: {"counter": int, ...}}``."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    # -- internals ---------------------------------------------------------

    def _ensure_dir(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    @contextlib.contextmanager
    def _locked(self):
        self._ensure_dir()
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        if not raw.strip():
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StateError(f"state file {self.path} is corrupt: {exc}") from exc
        if not isinstance(data, dict):
            raise StateError(f"state file {self.path} must contain a JSON object")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self._ensure_dir()
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise

    @staticmethod
    def _counter_of(data: dict[str, Any], name: str, default: int) -> int:
        entry = data.get(name)
        if not isinstance(entry, dict):
            return default
        value = entry.get("counter", default)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise StateError(f"counter for {name!r} is not a non-negative integer")
        return value

    # -- public API --------------------------------------------------------

    def counter(self, name: str, default: int = 0) -> int:
        """Read a counter without modifying the file."""
        return self._counter_of(self._read(), name, default)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return the whole state mapping (counters and timestamps only)."""
        return self._read()

    def take(self, name: str, default: int = 0) -> int:
        """Return the current counter and advance it by one, atomically.

        This is the only way a code should ever be issued: the counter is
        consumed whether or not the caller manages to use the code, which is
        exactly how HOTP behaves on the server side.
        """
        with self._locked():
            data = self._read()
            current = self._counter_of(data, name, default)
            entry = data.get(name)
            if not isinstance(entry, dict):
                entry = {}
            entry["counter"] = current + 1
            entry["updated_at"] = _utcnow()
            data[name] = entry
            self._write(data)
            return current

    def set_counter(self, name: str, value: int) -> None:
        """Force a counter value. For recovery after drift."""
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise StateError("counter must be a non-negative integer")
        with self._locked():
            data = self._read()
            entry = data.get(name)
            if not isinstance(entry, dict):
                entry = {}
            entry["counter"] = value
            entry["updated_at"] = _utcnow()
            data[name] = entry
            self._write(data)
