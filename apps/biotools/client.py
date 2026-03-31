"""
bio.tools API Client
====================
A minimal, self-contained HTTP client for the bio.tools REST API.

Docs: https://biotools.readthedocs.io/en/latest/api_reference_dev.html

Key endpoints used:
  GET https://bio.tools/api/tool/{id}?format=json
      Returns the full tool entry for a given bio.tools ID.

  GET https://bio.tools/api/tool/?name={name}&format=json
      Search by name (used for the form prefill suggestion feature).

The client uses only the standard library (urllib) to avoid adding
a hard dependency on `requests` for this single use case.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

BIOTOOLS_API_BASE = "https://bio.tools/api"
DEFAULT_TIMEOUT = 15  # seconds


@dataclass
class BioToolsToolEntry:
    """
    Parsed representation of a bio.tools tool API response.

    All fields map directly to the bio.tools JSON schema.
    See: https://biotoolsschema.readthedocs.io/
    """

    biotools_id: str  # biotoolsID
    name: str  # name
    description: str  # description
    homepage: str  # homepage
    version: list[str] = field(default_factory=list)  # version
    tool_type: list[str] = field(default_factory=list)  # toolType
    operating_system: list[str] = field(default_factory=list)  # operatingSystem
    license: str = ""  # license (SPDX)
    maturity: str = ""  # maturity
    cost: str = ""  # cost
    # Topics: list of {uri, term}
    edam_topics: list[dict] = field(default_factory=list)
    # Function blocks (operations + inputs + outputs)
    functions: list[dict] = field(default_factory=list)
    # Publications: list of {pmid, doi, pmcid, type, note, metadata}
    publications: list[dict] = field(default_factory=list)
    # Documentation links: list of {url, type}
    documentation: list[dict] = field(default_factory=list)
    # Download links: list of {url, type, version}
    download: list[dict] = field(default_factory=list)
    # Other links: list of {url, type}
    links: list[dict] = field(default_factory=list)
    # Full raw JSON for storage
    raw: dict = field(default_factory=dict)


class BioToolsError(Exception):
    """Raised when the bio.tools API returns an error or unexpected response."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class BioToolsNotFound(BioToolsError):
    """Raised when the requested bio.tools ID does not exist."""


class BioToolsClient:
    """
    Thin wrapper around the bio.tools REST API.

    Usage:
        client = BioToolsClient()
        tool = client.get_tool("blast")
        print(tool.name, tool.edam_topics)
    """

    def __init__(
        self,
        base_url: str = BIOTOOLS_API_BASE,
        timeout: int = DEFAULT_TIMEOUT,
        user_agent: str = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_agent = user_agent or "denbi-registry/1.0 (https://bio.tools)"

    def _get(self, path: str, params: dict | None = None) -> dict:
        """Make a GET request and return parsed JSON."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        logger.debug("bio.tools GET %s", url)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310
                body = resp.read()
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise BioToolsNotFound(
                    f"bio.tools tool not found (HTTP 404): {url}", status_code=404
                )
            raise BioToolsError(
                f"bio.tools API error (HTTP {exc.code}): {exc.reason}",
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise BioToolsError(f"bio.tools network error: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise BioToolsError(f"bio.tools returned invalid JSON: {exc}") from exc

    def get_tool(self, biotools_id: str) -> BioToolsToolEntry:
        """
        Fetch a single tool entry by its bio.tools ID.

        Args:
            biotools_id: The tool slug, e.g. 'blast', 'interproscan'.
                         Case-insensitive in bio.tools but returned as stored.

        Returns:
            BioToolsToolEntry with all fields populated.

        Raises:
            BioToolsNotFound: If no tool with this ID exists.
            BioToolsError: On network or API errors.
        """
        raw = self._get(f"tool/{biotools_id}", params={"format": "json"})
        return self._parse_tool(raw)

    def search_by_name(
        self, name: str, max_results: int = 5
    ) -> list[BioToolsToolEntry]:
        """
        Search bio.tools by tool name.

        Used for the form prefill suggestion — returns a short list
        of candidate matches the user can choose from.

        Args:
            name: Search string (tool name or fragment).
            max_results: Maximum number of results to return.

        Returns:
            List of BioToolsToolEntry objects (may be empty).
        """
        data = self._get(
            "tool", params={"name": name, "format": "json", "page_size": max_results}
        )
        entries = []
        for item in data.get("list", []):
            try:
                entries.append(self._parse_tool(item))
            except Exception as exc:
                logger.warning("Failed to parse bio.tools search result: %s", exc)
        return entries

    @staticmethod
    def _parse_tool(raw: dict[str, Any]) -> BioToolsToolEntry:
        """
        Parse a raw bio.tools API response dict into a BioToolsToolEntry.

        The bio.tools schema is complex and some fields may be absent or null.
        We defensively extract everything and fall back to safe defaults.
        """

        def _str(v: Any, default: str = "") -> str:
            return str(v).strip() if v else default

        def _list(v: Any) -> list:
            if isinstance(v, list):
                return v
            return [v] if v else []

        # Topics: [{uri, term}]
        edam_topics = [
            {"uri": t.get("uri", ""), "term": t.get("term", "")}
            for t in _list(raw.get("topic"))
            if t.get("uri")
        ]

        # Functions: each is {operation: [...], input: [...], output: [...], cmd, note}
        functions = []
        for func in _list(raw.get("function")):
            operations = [
                {"uri": op.get("uri", ""), "term": op.get("term", "")}
                for op in _list(func.get("operation"))
                if op.get("uri")
            ]
            inputs = [
                {
                    "data": {
                        "uri": inp.get("data", {}).get("uri", ""),
                        "term": inp.get("data", {}).get("term", ""),
                    },
                    "formats": [
                        {"uri": fmt.get("uri", ""), "term": fmt.get("term", "")}
                        for fmt in _list(inp.get("format"))
                        if fmt.get("uri")
                    ],
                }
                for inp in _list(func.get("input"))
            ]
            outputs = [
                {
                    "data": {
                        "uri": out.get("data", {}).get("uri", ""),
                        "term": out.get("data", {}).get("term", ""),
                    },
                    "formats": [
                        {"uri": fmt.get("uri", ""), "term": fmt.get("term", "")}
                        for fmt in _list(out.get("format"))
                        if fmt.get("uri")
                    ],
                }
                for out in _list(func.get("output"))
            ]
            functions.append(
                {
                    "operations": operations,
                    "inputs": inputs,
                    "outputs": outputs,
                    "cmd": _str(func.get("cmd")),
                    "note": _str(func.get("note")),
                }
            )

        # Publications
        publications = [
            {
                "pmid": _str(pub.get("pmid")),
                "doi": _str(pub.get("doi")),
                "pmcid": _str(pub.get("pmcid")),
                "type": _str(pub.get("type")),
                "note": _str(pub.get("note")),
            }
            for pub in _list(raw.get("publication"))
        ]

        # Documentation
        documentation = [
            {"url": _str(doc.get("url")), "type": _str(doc.get("type"))}
            for doc in _list(raw.get("documentation"))
            if doc.get("url")
        ]

        # Downloads
        download = [
            {
                "url": _str(d.get("url")),
                "type": _str(d.get("type")),
                "version": _str(d.get("version")),
            }
            for d in _list(raw.get("download"))
            if d.get("url")
        ]

        # Links
        links = [
            {"url": _str(lnk.get("url")), "type": _str(lnk.get("type"))}
            for lnk in _list(raw.get("link"))
            if lnk.get("url")
        ]

        # Version list — bio.tools stores versions as list of strings
        versions = [_str(v) for v in _list(raw.get("version")) if v]

        return BioToolsToolEntry(
            biotools_id=_str(raw.get("biotoolsID") or raw.get("name", "")),
            name=_str(raw.get("name")),
            description=_str(raw.get("description")),
            homepage=_str(raw.get("homepage")),
            version=versions,
            tool_type=[_str(t) for t in _list(raw.get("toolType"))],
            operating_system=[_str(o) for o in _list(raw.get("operatingSystem"))],
            license=_str(raw.get("license")),
            maturity=_str(raw.get("maturity")),
            cost=_str(raw.get("cost")),
            edam_topics=edam_topics,
            functions=functions,
            publications=publications,
            documentation=documentation,
            download=download,
            links=links,
            raw=raw,
        )
