"""Row locks taken by a full save of a submission."""

import pytest

from tests.factories import (
    ServiceSubmissionFactory,
)

pytestmark = pytest.mark.django_db


def _approved(**kw):
    """Approved in the database (the factory writes the status directly)."""
    return ServiceSubmissionFactory(status="approved", biotools_url="", **kw)


class TestLocking:
    """SQLite ignores row locks, so these check that the save asks for the
    submission row lock before it compares what it is about to write."""

    def test_a_full_save_locks_the_row_it_compares_the_status_with(self, locked_models):
        sub = _approved()
        del locked_models[:]
        sub.comments = "Edited"
        sub.save()
        assert locked_models == ["ServiceSubmission"]

    def test_a_save_of_listed_fields_needs_no_comparison(self, locked_models):
        sub = _approved()
        del locked_models[:]
        sub.comments = "Edited"
        sub.save(update_fields=["comments"])
        assert locked_models == []
