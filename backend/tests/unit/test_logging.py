"""JSON logging and request ids (task P3.6). No services needed."""

import io
import json
import logging

import pytest

from app.core.logging import JsonFormatter, configure_logging, request_id_var
from app.core.observability import pick_request_id


def record(
    msg: str = "hello %s", args: tuple[object, ...] = ("world",), **extra: object
) -> logging.LogRecord:
    r = logging.LogRecord("test.logger", logging.WARNING, __file__, 1, msg, args, None)
    for key, value in extra.items():
        setattr(r, key, value)
    return r


def test_a_line_is_one_json_object_with_the_documented_fields() -> None:
    line = JsonFormatter().format(record())
    assert "\n" not in line  # one object per line
    data = json.loads(line)
    assert data["msg"] == "hello world"  # the %s arguments are applied
    assert data["level"] == "WARNING" and data["logger"] == "test.logger"
    assert data["ts"].endswith("+00:00")  # UTC, ISO-8601, millisecond precision
    assert "request_id" not in data  # no request in progress


def test_the_request_id_of_the_current_request_is_added_to_every_line() -> None:
    token = request_id_var.set("req-123")
    try:
        assert json.loads(JsonFormatter().format(record()))["request_id"] == "req-123"
    finally:
        request_id_var.reset(token)
    assert "request_id" not in json.loads(JsonFormatter().format(record()))


def test_extra_fields_are_merged_and_unserialisable_values_cannot_break_logging() -> None:
    fields = {"status": 302, "duration_ms": 1.5, "weird": object()}
    data = json.loads(JsonFormatter().format(record(fields=fields)))
    assert data["status"] == 302 and data["duration_ms"] == 1.5
    assert data["weird"].startswith("<object")  # str() fallback, no exception


def test_an_exception_is_included_as_text() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        r = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    data = json.loads(JsonFormatter().format(r))
    assert "ValueError: boom" in data["exc"] and "Traceback" in data["exc"]


def test_newlines_in_a_message_cannot_forge_a_second_log_line() -> None:
    line = JsonFormatter().format(record("%s", ('user input\n{"level":"CRITICAL"}',)))
    assert "\n" not in line  # escaped inside the JSON string
    assert json.loads(line)["level"] == "WARNING"


def test_configure_logging_writes_json_to_the_given_stream_and_is_repeatable() -> None:
    first, second = io.StringIO(), io.StringIO()
    configure_logging("INFO", stream=first)
    configure_logging("INFO", stream=second)  # replaces ITS OWN handler, does not stack
    logging.getLogger("app.test").info("one line")
    assert first.getvalue() == ""
    (parsed,) = [json.loads(ln) for ln in second.getvalue().splitlines() if "one line" in ln]
    assert parsed["msg"] == "one line"
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_linkforge_json_handler", False)]
    assert len(ours) == 1


def test_uvicorns_access_log_is_switched_off_because_it_prints_client_addresses() -> None:
    configure_logging("INFO", stream=io.StringIO())
    assert logging.getLogger("uvicorn.access").disabled is True


# --- request ids ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "good", ["abc123", "req-1.2_3", "A" * 64, "0f8fad5b-d9cb-469f-a165-70867728950e"]
)
def test_a_sane_incoming_request_id_is_kept(good: str) -> None:
    assert pick_request_id(good) == good


@pytest.mark.parametrize(
    "bad", [None, "", "has space", "a" * 65, "line\nbreak", "semi;colon", "ünï", "<script>", "../x"]
)
def test_anything_else_is_replaced_by_a_fresh_id(bad: str | None) -> None:
    new = pick_request_id(bad)
    assert new != bad and len(new) == 32 and new.isalnum()  # a uuid4 in hex
    assert pick_request_id(bad) != new  # and a different one every time
