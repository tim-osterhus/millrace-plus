"""Shared framing is bounded; native failure vocabularies remain distinct."""

from __future__ import annotations

import hashlib

import pytest
from millrace.router.execution import RouterError

from millrace_plus.router import stream_records
from millrace_plus.router.codex import Stream as CodexStream
from millrace_plus.router.opencode import Stream as OpenCodeStream
from millrace_plus.router.stream_records import FramingError, JsonLineRecords


def test_bounds_utf8_eof_and_native_failure_mapping() -> None:
    raw = '{"text":"雪"}\n{"tail":true}'.encode()
    records = JsonLineRecords(total_limit=100, record_limit=30)
    values: list[dict[str, object]] = []
    for byte in raw:
        records.feed(bytes([byte]), values.append)
    records.finish(values.append)
    assert values == [{"text": "雪"}, {"tail": True}]
    assert records.bytes == len(raw)
    assert records.hash.hexdigest() == hashlib.sha256(raw).hexdigest()
    assert not records.buffer
    for native, expected in (
        (CodexStream(), "codex_invalid_events"),
        (OpenCodeStream(), "duplicate_key"),
    ):
        with pytest.raises(RouterError) as caught:
            native.feed(b'{"type":"x","type":"y"}\n')
        assert caught.value.code == expected
        assert native.invalid


@pytest.mark.parametrize(
    "raw,code",
    [
        (b'{"a":1,"a":2}\n', "invalid_record"),
        (b'{"a":"\xff"}\n', "invalid_record"),
        (b'[]\n', "invalid_record"),
        (b'{"a":NaN}\n', "invalid_record"),
        (b'{"a":', "invalid_record"),
        (b'x' * 31, "line_limit"),
    ],
)
def test_invalid_records_clear_buffer_and_keep_observed_digest(raw, code) -> None:
    records = JsonLineRecords(
        total_limit=10000, record_limit=5000 if len(raw) > 100 else 30
    )
    with pytest.raises(FramingError, match=code):
        records.feed(raw, lambda _: None)
        records.finish(lambda _: None)
    assert records.invalid and not records.buffer
    records.feed(b"cleanup bytes", lambda _: pytest.fail("parsed after failure"))
    assert records.bytes == len(raw) + len(b"cleanup bytes")
    expected = hashlib.sha256(raw + b"cleanup bytes").hexdigest()
    assert records.hash.hexdigest() == expected


def test_decoder_recursion_error_maps_to_invalid_record(monkeypatch) -> None:
    def recursion(_):
        raise RecursionError("test-owned decoder recursion failure")

    monkeypatch.setattr(stream_records, "strict_json", recursion)
    records = JsonLineRecords(total_limit=100, record_limit=30)
    with pytest.raises(FramingError, match="invalid_record"):
        records.feed(b'{"deep":0}\n', lambda _: pytest.fail("consumed invalid record"))
    assert records.invalid and not records.buffer
    records.feed(b"cleanup", lambda _: pytest.fail("consumed after failure"))
    assert records.bytes == len(b'{"deep":0}\ncleanup')
    expected = hashlib.sha256(b'{"deep":0}\ncleanup').hexdigest()
    assert records.hash.hexdigest() == expected


def test_record_order_before_later_failure_and_total_limit() -> None:
    records = JsonLineRecords(total_limit=100, record_limit=30)
    values: list[dict[str, object]] = []
    with pytest.raises(FramingError):
        records.feed(b'{"first":1}\n{bad}\n', values.append)
    assert values == [{"first": 1}]
    records = JsonLineRecords(total_limit=3, record_limit=30)
    with pytest.raises(FramingError, match="event_limit"):
        records.feed(b"{}\n{}\n", values.append)
    assert not records.buffer
