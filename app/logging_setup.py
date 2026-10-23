"""Logging configuration.

Configured once, at startup. Every module gets its own logger through
logging.getLogger(__name__), so log lines show which module produced them.

In production we emit JSON, because Google Cloud Logging parses JSON into
searchable fields. Locally we emit plain text, which is easier to read.
"""

import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    """Render a log record as a single JSON object.

    Cloud Logging parses JSON into searchable fields, so request_id and
    tenant_id become things you can filter on rather than text to scroll.
    """

    def format(self, record: logging.LogRecord) -> str:
        from app.request_context import conversation_id, request_id, tenant_id

        payload = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }

        rid = request_id.get()
        if rid:
            payload["request_id"] = rid

        tid = tenant_id.get()
        if tid:
            payload["tenant_id"] = tid

        cid = conversation_id.get()
        if cid:
            payload["conversation_id"] = cid

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload)

def setup_logging(level: str = "INFO", as_json: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Remove existing handlers so repeated calls don't duplicate every line.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stdout)
    if as_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%H:%M:%S",
            )
        )
    root.addHandler(handler)

    # Quieten noisy libraries unless we're debugging.
    if level.upper() != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("google").setLevel(logging.WARNING)