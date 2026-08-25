"""Tests for apps.submissions.middleware.RequestIDMiddleware.

Regression guard for a serious class of bug: per-request work that mutates
process-global state unboundedly. The middleware used to call
``logging.setLogRecordFactory`` on *every* request, each time wrapping the
already-wrapped factory — so the chain grew one layer per request (leaking each
request's closure) until any log call raised ``RecursionError`` and the server
returned hung/empty responses.
"""

import logging

import pytest
from django.http import HttpResponse

from apps.submissions.middleware import RequestIDMiddleware


@pytest.fixture(autouse=True)
def _restore_log_factory():
    """Isolate the process-global log record factory across tests."""
    saved = logging.getLogRecordFactory()
    yield
    logging.setLogRecordFactory(saved)


def _mw(get_response=None):
    return RequestIDMiddleware(get_response or (lambda request: HttpResponse("ok")))


def test_sets_request_id_attr_and_response_header(rf):
    resp = _mw()(rf.get("/"))
    assert "X-Request-ID" in resp
    assert len(resp["X-Request-ID"]) >= 32  # a uuid4 string


def test_log_records_carry_request_id_during_request(rf):
    captured = {}

    def get_response(request):
        rec = logging.getLogRecordFactory()("t", logging.INFO, "f", 1, "msg", (), None)
        captured["rid"] = getattr(rec, "request_id", None)
        return HttpResponse("ok")

    resp = _mw(get_response)(rf.get("/"))
    assert captured["rid"], "request_id should be attached to log records mid-request"
    assert captured["rid"] == resp["X-Request-ID"]


def test_does_not_rechain_log_factory_per_request(rf):
    """The record factory must be installed once, not re-wrapped per request."""
    mw = _mw()
    mw(rf.get("/"))
    factory = logging.getLogRecordFactory()
    for _ in range(200):
        mw(rf.get("/"))
    assert logging.getLogRecordFactory() is factory, (
        "factory was re-wrapped per request — the chain will grow without bound"
    )


def test_many_requests_do_not_overflow_the_log_factory(rf):
    """Production symptom: after more than a recursion-limit's worth of requests,
    creating a log record (what every logging call does) must still succeed."""
    mw = _mw()
    for _ in range(1500):  # > sys default recursion limit (1000)
        mw(rf.get("/"))
    record = logging.getLogRecordFactory()("t", logging.INFO, "f", 1, "msg", (), None)
    assert hasattr(record, "request_id")


@pytest.mark.django_db
def test_flow_request_id_header_unique_and_stack_stays_healthy(client):
    """Through the real middleware stack: every response carries a unique
    X-Request-ID and a burst of requests never degrades (the recursion bug made
    later requests fail)."""
    seen = set()
    for _ in range(25):
        resp = client.get("/health/live/")
        assert resp.status_code == 200
        assert "X-Request-ID" in resp
        seen.add(resp["X-Request-ID"])
    assert len(seen) == 25  # a fresh id per request, no collisions
