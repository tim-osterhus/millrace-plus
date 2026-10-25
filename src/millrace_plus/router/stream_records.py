"""Bounded observed-byte accounting and strict object JSONL framing."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from .configuration import RouterError, mapping, strict_json

__all__ = ("FramingError", "JsonLineRecords")


class FramingError(ValueError):
    """A fixed framing code; native adapters retain their own error mapping."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class JsonLineRecords:
    """Frame records without retaining full output or interpreting native events.

    Once invalid, later observed chunks update only counters and the digest.
    EOF accepts a complete final object without a newline.
    """

    def __init__(self, *, total_limit: int, record_limit: int) -> None:
        if any(type(n) is not int or n <= 0 for n in (total_limit, record_limit)):
            raise ValueError("invalid_record_limits")
        self.total_limit = total_limit
        self.record_limit = record_limit
        self.buffer = bytearray()
        self.hash = hashlib.sha256()
        self.bytes = 0
        self.invalid = False

    def _record(self, raw: bytes, consume: Callable[[dict[str, object]], None]) -> None:
        if len(raw) > self.record_limit:
            raise FramingError("line_limit")
        try:
            record = mapping(strict_json(raw))
        except (RouterError, ValueError, UnicodeError, RecursionError) as exc:
            raise FramingError("invalid_record") from exc
        consume(record)

    def feed(self, raw: bytes, consume: Callable[[dict[str, object]], None]) -> None:
        """Observe bytes, then deliver each complete record in wire order."""
        self.bytes += len(raw)
        self.hash.update(raw)
        if self.invalid:
            return
        try:
            if self.bytes > self.total_limit:
                raise FramingError("event_limit")
            # Each retained partial record is bounded independently of chunk size.
            start = 0
            while True:
                end = raw.find(b"\n", start)
                tail = raw[start:] if end < 0 else raw[start:end]
                if len(self.buffer) + len(tail) > self.record_limit:
                    raise FramingError("line_limit")
                self.buffer.extend(tail)
                if end < 0:
                    break
                self._record(bytes(self.buffer), consume)
                self.buffer.clear()
                start = end + 1
        except Exception:
            self.invalid = True
            self.buffer.clear()
            raise

    def finish(self, consume: Callable[[dict[str, object]], None]) -> None:
        """Validate the remaining final record only after established EOF."""
        if self.invalid:
            raise FramingError("invalid_record")
        try:
            if self.buffer:
                self._record(bytes(self.buffer), consume)
                self.buffer.clear()
        except Exception:
            self.invalid = True
            self.buffer.clear()
            raise
