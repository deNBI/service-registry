"""Only apps/catalogue/publishing.py and signals.py write the publishing models.

The scan is a heuristic over common code shapes: writes through the model
names and through the reverse accessors. A row held in a variable and saved
later is not visible to it; ``verify_published`` (``drift``) is the run-time
check for the published copy.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {"apps/catalogue/publishing.py", "apps/catalogue/signals.py"}
MODELS = {"PublishedService", "PublishedRevision", "WithdrawnService"}
# Reverse accessors from submissions and reference rows to the publishing models.
ACCESSORS = {
    "published",
    "published_revisions",
    "published_services",
    "published_by_topic",
    "published_by_operation",
}
WRITE_METHODS = {
    "create",
    "update",
    "update_or_create",
    "get_or_create",
    "bulk_create",
    "bulk_update",
    "delete",
    "save",
    "add",
    "set",
    "remove",
    "clear",
}


def _mentions_model(node: ast.AST) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id in MODELS)
        or (isinstance(n, ast.Attribute) and n.attr in ACCESSORS)
        for n in ast.walk(node)
    )


def _violations(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in MODELS:
            lines.append(node.lineno)  # instantiating a row to save it
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in WRITE_METHODS
            and _mentions_model(func.value)
        ):
            lines.append(node.lineno)
    return sorted(lines)


def test_detector_flags_known_patterns():
    src = (
        "PublishedService.objects.create(pk=1)\n"
        "PublishedService.objects.filter(pk=1).update(state='x')\n"
        "PublishedRevision.objects.filter(pk=1).delete()\n"
        "WithdrawnService(id=1).save()\n"
        "WithdrawnService.objects.get_or_create(id=1)\n"
        "PublishedService.objects.bulk_create([])\n"
        "sub.published.delete()\n"
        "sub.published_revisions.create(number=1)\n"
        "sub.published.service_categories.add(cat)\n"
        "category.published_services.clear()\n"
    )
    assert _violations(src) == [1, 2, 3, 4, 4, 5, 6, 7, 8, 9, 10]


def test_detector_ignores_reads():
    src = (
        "PublishedService.objects.filter(pk=1).exists()\n"
        "PublishedService.objects.get(pk=1)\n"
        "PublishedRevision.objects.order_by('number').first()\n"
        "ps.service_categories.all()\n"
        "obj.published_services.values_list('pk', flat=True)\n"
        "sub.published.service_name\n"
    )
    assert _violations(src) == []


def test_publishing_models_have_one_writer():
    offenders = {}
    for path in sorted((ROOT / "apps").rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        if rel in ALLOWED or "/migrations/" in rel or rel == "apps/catalogue/models.py":
            continue
        lines = _violations(path.read_text())
        if lines:
            offenders[rel] = lines
    assert offenders == {}
