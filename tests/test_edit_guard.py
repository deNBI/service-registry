"""Edits go through apps/submissions/edits.py.

The owner web view, the API view and the admin module must not publish, or
change a status for an edit, themselves: a second copy of that logic would
let the three entry points drift apart. What they may still call is listed
here. The scan is a heuristic over call shapes: a function held in a
variable is not visible to it.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARDED = (
    "apps/submissions/views.py",
    "apps/api/views.py",
    "apps/submissions/admin.py",
)
CALLS = {"publish", "republish", "set_status"}
# (file, enclosing function, call): status actions that are not edits.
ALLOWED = {
    # The owner's deprecate button.
    ("apps/submissions/views.py", "EditView.post", "set_status"),
    # The status buttons and bulk actions of the admin.
    (
        "apps/submissions/admin.py",
        "ServiceSubmissionAdmin._change_status",
        "set_status",
    ),
    # The bulk change of maturity tags: not a form save.
    (
        "apps/submissions/admin.py",
        "ServiceSubmissionAdmin.action_assign_maturity_tags",
        "republish",
    ),
}


def _calls(source: str) -> list[tuple[str, str, int, bool]]:
    """(enclosing function, call name, line, passes owner_edit) per watched call."""
    found = []

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, [*scope, child.name])
                continue
            if isinstance(child, ast.Call):
                func = child.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else getattr(func, "id", "")
                )
                if name in CALLS:
                    owner_edit = any(k.arg == "owner_edit" for k in child.keywords)
                    found.append((".".join(scope), name, child.lineno, owner_edit))
            visit(child, scope)

    visit(ast.parse(source), [])
    return found


def test_detector_flags_known_shapes():
    src = (
        "class A:\n"
        "    def f(self):\n"
        "        republish(sub, reason='x', actor='y')\n"
        "        mod.publish(sub)\n"
        "        set_status(sub, 'submitted', actor='y', owner_edit=True)\n"
        "def g():\n"
        "    set_status(sub, 'approved', actor='y')\n"
    )
    assert _calls(src) == [
        ("A.f", "republish", 3, False),
        ("A.f", "publish", 4, False),
        ("A.f", "set_status", 5, True),
        ("g", "set_status", 7, False),
    ]


def test_detector_ignores_other_calls():
    assert _calls("def f():\n    publish_label(sub)\n    obj.save()\n") == []


def test_only_listed_status_actions_remain_outside_edits():
    found = {}
    for rel in GUARDED:
        for scope, name, line, owner_edit in _calls((ROOT / rel).read_text()):
            if (rel, scope, name) not in ALLOWED or owner_edit:
                found.setdefault(rel, []).append((scope, name, line))
    assert found == {}, (
        f"publish or status change for an edit outside edits.py: {found}"
    )


def test_every_allowed_call_still_exists():
    seen = {
        (rel, scope, name)
        for rel in GUARDED
        for scope, name, _line, _owner in _calls((ROOT / rel).read_text())
    }
    assert ALLOWED <= seen, f"stale entries: {ALLOWED - seen}"
