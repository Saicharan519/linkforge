"""Structured JSON logging (backend/CLAUDE.md): one JSON object per line on stdout.

Why JSON: the platform (Kubernetes, later Loki or CloudWatch) collects stdout lines. A plain
sentence cannot be filtered by request, status or duration; a JSON object can.

Fields: `ts` (UTC ISO-8601), `level`, `logger`, `msg`, and `request_id` when the line belongs to a
request; access-log lines add `method`, `path`, `route`, `status`, `duration_ms`. Exceptions add
`exc` (the traceback).

What is NEVER logged: passwords, session ids, API keys, CSRF tokens, raw client IPs. The access log
therefore records no headers, no cookies, no query string and no client address, and uvicorn's own
access log (which prints the client address) is switched off in the image (`--no-access-log`).
"""

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

# The id of the request being handled, visible to every log line written while handling it. A
# ContextVar follows the request through `await`s without being passed around.
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

_HANDLER_MARK = "_linkforge_json_handler"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            data["request_id"] = request_id
        fields = getattr(record, "fields", None)  # logger.info("...", extra={"fields": {...}})
        if isinstance(fields, dict):
            data.update(fields)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        # default=str: an unexpected object in a field must never make logging itself fail.
        return json.dumps(data, default=str, ensure_ascii=True)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Send all logging to stdout as JSON. Safe to call again (it replaces its own handler only)."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own plain-text handlers; route its lines through ours instead.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True  # prints client addresses; we log ourselves
