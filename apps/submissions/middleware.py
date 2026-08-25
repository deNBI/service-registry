"""
Custom Middleware
================
RequestIDMiddleware: Injects a UUID per request, included in all log records
and error responses so support staff can correlate logs to issues.
"""

import contextvars
import logging
import uuid

# Current request's id, readable from anywhere in the (sync or async) call
# stack. A ContextVar is coroutine/thread-safe and needs no per-request global
# mutation — see the factory note below.
_request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

# Install the log-record factory exactly ONCE, at import time. It wraps whatever
# factory is current *now* and reads the request id from the ContextVar.
#
# Do NOT re-install this per request: logging.setLogRecordFactory chains onto the
# existing factory, so re-installing on every request grows the chain one layer
# per request (leaking each request's closure) until any log call overflows the
# stack with RecursionError and the server starts returning hung/empty responses.
_original_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs):
    record = _original_factory(*args, **kwargs)
    record.request_id = _request_id_var.get()
    return record


logging.setLogRecordFactory(_record_factory)


class RequestIDMiddleware:
    """
    Injects a unique ``request_id`` UUID into each request and exposes it to all
    logging calls for the duration of that request via a ContextVar. The
    request_id is included in JSON log output via the logging configuration and
    echoed back in the ``X-Request-ID`` response header.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = str(uuid.uuid4())
        token = _request_id_var.set(request.request_id)
        try:
            response = self.get_response(request)
        finally:
            # Restore the previous value so the id never leaks into unrelated
            # work reusing this thread/context.
            _request_id_var.reset(token)
        # Echo the request ID in the response for client-side correlation
        response["X-Request-ID"] = request.request_id
        return response
