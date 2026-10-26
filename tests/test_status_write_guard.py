"""Submission status may only be written by apps/submissions/lifecycle.py.

The scan is a heuristic over common code shapes. Shapes it cannot see (a
field list held in a variable, raw SQL) are caught at run time instead: the
status-history check after every database test.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {"apps/submissions/lifecycle.py"}
# Models with their own unrelated ``status`` column.
OTHER_STATUS_MODELS = ("SubmissionDeletionAudit",)
WRITE_CALLS = {"save", "create", "update", "update_or_create", "get_or_create"}


def _has_constant(node: ast.AST, value: str) -> bool:
    return any(isinstance(n, ast.Constant) and n.value == value for n in ast.walk(node))


def _names_status(node: ast.keyword) -> bool:
    """keyword ``status=``, or ``**{"status": ...}`` / ``**dict(status=...)``."""
    if node.arg == "status":
        return True
    if node.arg is not None:
        return False
    value = node.value
    if isinstance(value, ast.Dict):
        return any(
            isinstance(k, ast.Constant) and k.value == "status" for k in value.keys
        )
    return isinstance(value, ast.Call) and any(
        k.arg == "status" for k in value.keywords
    )


def _violations(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr == "status":
                    lines.append(node.lineno)
                elif isinstance(t, ast.Subscript) and _has_constant(t.slice, "status"):
                    lines.append(node.lineno)
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        target = ast.unparse(func)
        if any(model in target for model in OTHER_STATUS_MODELS):
            continue
        if name in WRITE_CALLS and any(_names_status(k) for k in node.keywords):
            lines.append(node.lineno)
        elif name == "bulk_create" and "ServiceSubmission" in target:
            # No post_save signal, so no creation event in the history.
            lines.append(node.lineno)
        elif (
            name == "setattr"
            and len(node.args) >= 2
            and _has_constant(node.args[1], "status")
        ):
            lines.append(node.lineno)
        elif name == "bulk_update" and any(
            _has_constant(a, "status")
            for a in [*node.args, *(k.value for k in node.keywords)]
        ):
            lines.append(node.lineno)
        elif name == "save" and any(
            k.arg == "update_fields" and _has_constant(k.value, "status")
            for k in node.keywords
        ):
            lines.append(node.lineno)
    return lines


def test_detector_flags_known_patterns():
    src = (
        "sub.status = 'x'\n"
        "serializer.save(status='x')\n"
        "Model.objects.create(status='x')\n"
        "qs.update(status='x')\n"
        "setattr(sub, 'status', 'x')\n"
        "sub.save(update_fields=['status'])\n"
        "Model.objects.bulk_update(objs, ['status'])\n"
        "qs.filter(pk=1).update(status='x')\n"
        "qs.update(**{'status': 'x'})\n"
        "qs.update(**dict(status='x'))\n"
        "Model.objects.bulk_update(objs, fields=['status'])\n"
        "sub.__dict__['status'] = 'x'\n"
        "ServiceSubmission.objects.bulk_create(objs)\n"
    )
    assert sorted(_violations(src)) == list(range(1, 14))


def test_detector_ignores_reads_and_http_status():
    src = (
        "if sub.status == 'x': pass\n"
        "qs.filter(status='x')\n"
        "render(request, 't.html', ctx, status=400)\n"
        "Response(data, status=201)\n"
        "sub.save(update_fields=['comments'])\n"
        "SubmissionDeletionAudit.objects.create(status=obj.status)\n"
        "qs.update(**{'comments': 'x'})\n"
        "Other.objects.bulk_create(objs)\n"
    )
    assert _violations(src) == []


def _guarded_files():
    for path in sorted((ROOT / "apps").rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        if "/migrations/" in rel or rel in ALLOWED:
            continue
        yield rel
    for path in sorted((ROOT / "scripts").glob("*.py")):
        yield str(path.relative_to(ROOT))


def test_no_status_writes_outside_lifecycle():
    found = {}
    for rel in _guarded_files():
        lines = _violations((ROOT / rel).read_text())
        if lines:
            found[rel] = lines
    assert found == {}, f"status written outside set_status(): {found}"
