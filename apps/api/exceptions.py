"""Custom DRF exception handler — adds request_id to error responses."""

import logging

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


class IdempotencyConflict(APIException):
    """A request with this Idempotency-Key was already handled (or is running)."""

    status_code = status.HTTP_409_CONFLICT
    default_detail = (
        "A request with this Idempotency-Key was already received, so no second "
        "service was registered. The API key of a service is shown only once, in "
        "the response to the first request."
    )
    default_code = "idempotency_conflict"


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is not None:
        request = context.get("request")
        request_id = getattr(request, "request_id", None) if request else None

        # Wrap error in a consistent envelope
        response.data = {
            "error": response.data,
            "request_id": request_id,
        }

        if response.status_code >= 500:
            logger.error(
                f"API 5xx error: {exc}",
                extra={"request_id": request_id},
                exc_info=True,
            )

    return response
