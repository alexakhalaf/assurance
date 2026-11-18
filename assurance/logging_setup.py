"""Structured logging. JSON lines in production, readable text in dev."""
from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime

request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id.get()
        if rid:
            doc["request_id"] = rid
        for k in ("snapshot_id", "device", "job_id"):
            if hasattr(record, k):
                doc[k] = getattr(record, k)
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, default=str)


def configure_logging(level: str = "INFO", as_json: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if as_json else
                         logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # pyATS/Unicon are chatty and can echo device sessions; keep them quiet.
    for noisy in ("unicon", "pyats", "genie", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
