"""
bio.tools Form Integration Views
=================================
HTMX endpoint called from the registration form when a user enters their
bio.tools URL or ID.

Flow:
  1. User types https://bio.tools/blast in the biotools_url field
  2. On blur, HTMX POSTs to /biotools/prefill/?id=blast
  3. This view fetches the tool from bio.tools (with a short timeout)
  4. Returns a JSON response with fields to prefill in the form
  5. Client-side JS populates the fields (with user confirmation prompt)

The prefill is always non-destructive: fields are only suggested, never
overwritten silently. The user must confirm before applying.
"""

import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django_ratelimit.decorators import ratelimit

from .client import BioToolsClient, BioToolsError, BioToolsNotFound

logger = logging.getLogger(__name__)


@ratelimit(key="ip", rate=settings.RATE_LIMIT_BIOTOOLS, method="GET", block=True)
@require_GET
def biotools_prefill(request):
    """
    GET /biotools/prefill/?id=<biotools_id>

    Fetches tool metadata from bio.tools and returns a JSON object
    containing suggested form field values.

    Response shape (200):
    {
      "found": true,
      "biotools_id": "blast",
      "name": "BLAST",
      "description": "...",
      "homepage": "https://blast.ncbi.nlm.nih.gov",
      "license": "Public domain",
      "publications": "2731737, 9254694",
      "edam_topics": [
        {"uri": "http://edamontology.org/topic_0080", "term": "Sequence analysis"}
      ],
      "edam_operations": [
        {"uri": "http://edamontology.org/operation_0346", "term": "Sequence similarity search"}
      ],
      "tool_types": ["Command-line tool", "Web application"],
      "github_url": "",
      "message": "Data prefilled from bio.tools. Please review before saving."
    }

    Response shape (404):
    {
      "found": false,
      "error": "bio.tools ID 'xyz' not found"
    }

    Response shape (error):
    {
      "found": false,
      "error": "bio.tools API is temporarily unavailable. Please fill in manually."
    }
    """
    biotools_id = request.GET.get("id", "").strip().lower()
    if not biotools_id:
        return JsonResponse(
            {"found": False, "error": "No bio.tools ID provided."}, status=400
        )

    # Strip full URL if the user pasted the URL instead of just the ID
    if biotools_id.startswith("https://bio.tools/"):
        biotools_id = biotools_id[len("https://bio.tools/") :].rstrip("/")

    client = BioToolsClient(timeout=8)
    try:
        tool = client.get_tool(biotools_id)
    except BioToolsNotFound:
        return JsonResponse(
            {
                "found": False,
                "error": f"No tool with ID '{biotools_id}' found in bio.tools.",
            },
            status=404,
        )
    except BioToolsError as exc:
        logger.warning("bio.tools prefill error for '%s': %s", biotools_id, exc)
        return JsonResponse(
            {
                "found": False,
                "error": "bio.tools is temporarily unavailable. Please fill in manually.",
            },
            status=503,
        )

    # Collect all EDAM Operations from all function blocks
    edam_operations = []
    seen_op_uris = set()
    for func in tool.functions:
        for op in func.get("operations", []):
            if op.get("uri") and op["uri"] not in seen_op_uris:
                edam_operations.append(op)
                seen_op_uris.add(op["uri"])

    # Convert publications to our comma-separated PMID/DOI format
    pub_refs = []
    for pub in tool.publications:
        if pub.get("pmid"):
            pub_refs.append(pub["pmid"])
        elif pub.get("doi"):
            pub_refs.append(pub["doi"])
    publications_str = ", ".join(pub_refs[:10])  # cap at 10

    # Extract GitHub URL from links if present
    github_url = ""
    for link in tool.links:
        url = link.get("url", "")
        if "github.com" in url:
            github_url = url
            break

    # Extract source code link from downloads
    source_url = ""
    for dl in tool.download:
        if dl.get("type") in ("Source code", "Source package"):
            if "github.com" in dl.get("url", ""):
                source_url = dl["url"]
                break

    return JsonResponse(
        {
            "found": True,
            "biotools_id": tool.biotools_id,
            "name": tool.name,
            "description": tool.description[:2000],
            "homepage": tool.homepage,
            "license": tool.license,
            "publications": publications_str,
            "edam_topics": tool.edam_topics,
            "edam_operations": edam_operations,
            "tool_types": tool.tool_type,
            "github_url": github_url or source_url,
            "version": tool.version[0] if tool.version else "",
            "message": (
                "Metadata found in bio.tools. "
                "Fields below have been pre-filled — please review and adjust before submitting."
            ),
        }
    )


@ratelimit(key="ip", rate=settings.RATE_LIMIT_BIOTOOLS, method="GET", block=True)
@require_GET
def biotools_search(request):
    """
    GET /biotools/search/?q=<name>

    Searches bio.tools by tool name and returns a short list of candidates.
    Used to power the name-based lookup suggestion in the form.

    Response:
    {
      "results": [
        {"biotools_id": "blast", "name": "BLAST", "description": "..."},
        ...
      ]
    }
    """
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})

    client = BioToolsClient(timeout=8)
    try:
        tools = client.search_by_name(query, max_results=8)
    except BioToolsError:
        return JsonResponse({"results": []})

    return JsonResponse(
        {
            "results": [
                {
                    "biotools_id": t.biotools_id,
                    "name": t.name,
                    "description": (t.description[:150] + "…")
                    if len(t.description) > 150
                    else t.description,
                    "homepage": t.homepage,
                }
                for t in tools
            ]
        }
    )
