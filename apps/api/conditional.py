"""Entity tags and If-Match for the submission detail endpoint (RFC 9110).

The tag is a hash of the JSON representation a GET returns, so it changes
whenever that representation changes (including the embedded bio.tools
record), as a strong validator must.
"""

import hashlib
import json

ANY = "*"


def entity_tag(data) -> str:
    """Strong entity tag of a serialized representation."""
    body = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return '"' + hashlib.sha256(body.encode()).hexdigest()[:32] + '"'


def if_match_allows(header: str | None, current: str) -> bool:
    """Whether an If-Match header value lets a change of ``current`` proceed.

    No header: yes. ``*``: yes (the submission exists). Otherwise one of the
    listed tags must equal ``current``; If-Match uses the strong comparison,
    so a weak tag (``W/"..."``) never matches.
    """
    if header is None:
        return True
    if header.strip() == ANY:
        return True
    return any(tag.strip() == current for tag in header.split(","))
