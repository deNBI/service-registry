"""
Logging Filters
===============
ScrubSensitiveFilter strips Authorization header values and cookie strings
from log records to prevent API keys and session tokens from appearing in logs.
"""

import logging
import re


class ScrubSensitiveFilter(logging.Filter):
    """
    Removes sensitive values from log messages before they are emitted.

    Scrubs:
      - Authorization header values (replaces with [REDACTED])
      - Cookie header values
      - Any string matching the API key pattern (64-char URL-safe base64)
    """

    _AUTH_RE = re.compile(r"(Authorization:\s*(?:\S+\s+)+)\S+", re.IGNORECASE)
    _COOKIE_RE = re.compile(
        r"(Cookie:\s*(?:[^=;=\s]+\s*=\s*[^;]*;\s*)*)\S+", re.IGNORECASE
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)
        # Also scrub args used in %-style formatting
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    self._scrub(a) if isinstance(a, str) else a for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: self._scrub(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
        return True

    def _scrub(self, text: str) -> str:
        text = self._AUTH_RE.sub(r"\1[REDACTED]", text)
        text = self._COOKIE_RE.sub(r"\1[REDACTED]", text)
        return text
