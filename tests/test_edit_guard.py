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
PUBLISHING = "apps/catalogue/publishing.py"
# Public functions of the publishing module that do not write the published
# copy (they build, compare or read content, or write the working copy). Every
# other public function there is watched, so a new writer is covered at once.
NOT_WRITERS = {
    "build_content",
    "content_hash",
    "complete_content",
    "existing_references",
    "restorable_content",
    "names_deleted_foreign_key",
    "expected_flags",
    "apply_content_to_working_copy",
}


def _public_functions(source: str) -> set[str]:
    return {
        node.name
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }


def _watched(publishing_source: str) -> set[str]:
    """Names of the functions that write the published copy, and set_status."""
    return (_public_functions(publishing_source) - NOT_WRITERS) | {"set_status"}


CALLS = _watched((ROOT / PUBLISHING).read_text())
# (file, enclosing function, call, target): status actions that are not edits.
# The target is the status given to set_status, or the reason given to
# republish, as written in the source.
ALLOWED = {
    # The owner's deprecate button.
    (
        "apps/submissions/views.py",
        "EditView.post",
        "set_status",
        "SubmissionStatus.DEPRECATED",
    ),
    # The status buttons and bulk actions of the admin.
    (
        "apps/submissions/admin.py",
        "ServiceSubmissionAdmin._change_status",
        "set_status",
        "new_status",
    ),
    # The bulk change of maturity tags: not a form save.
    (
        "apps/submissions/admin.py",
        "ServiceSubmissionAdmin.action_assign_maturity_tags",
        "republish",
        "'tag_change'",
    ),
}


def _target(call: ast.Call, name: str) -> str:
    """The status argument of set_status, or the reason argument of republish."""
    if name == "set_status":
        if len(call.args) > 1:
            return ast.unparse(call.args[1])
        keyword = "new_status"
    elif name == "republish":
        keyword = "reason"
    else:
        return ""
    for k in call.keywords:
        if k.arg == keyword:
            return ast.unparse(k.value)
    return ""


def _aliases(tree: ast.AST, watched: set[str]) -> dict[str, str]:
    """Local names that stand for a watched function (``import X as Y``)."""
    return {
        alias.asname: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname and alias.name in watched
    }


def _calls(
    source: str, watched: set[str] | None = None
) -> list[tuple[str, str, int, bool, str]]:
    """(enclosing function, call, line, passes owner_edit, target) per watched
    call. A call through an alias is reported under the function's own name."""
    watched = CALLS if watched is None else watched
    tree = ast.parse(source)
    aliases = _aliases(tree, watched)
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
                name = aliases.get(name, name)
                if name in watched:
                    owner_edit = any(k.arg == "owner_edit" for k in child.keywords)
                    found.append(
                        (
                            ".".join(scope),
                            name,
                            child.lineno,
                            owner_edit,
                            _target(child, name),
                        )
                    )
            visit(child, scope)

    visit(tree, [])
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
        ("A.f", "republish", 3, False, "'x'"),
        ("A.f", "publish", 4, False, ""),
        ("A.f", "set_status", 5, True, "'submitted'"),
        ("g", "set_status", 7, False, "'approved'"),
    ]


def test_detector_reads_the_status_given_as_a_keyword():
    src = "def f():\n    set_status(sub, new_status=DEPRECATED, actor='y')\n"
    assert _calls(src) == [("f", "set_status", 2, False, "DEPRECATED")]


def test_detector_follows_an_aliased_import():
    src = (
        "from apps.catalogue.publishing import republish as rp\n"
        "def f():\n"
        "    rp(sub, reason='x', actor='y')\n"
    )
    assert _calls(src) == [("f", "republish", 3, False, "'x'")]


def test_detector_follows_an_aliased_import_inside_a_function():
    src = (
        "def f():\n"
        "    from apps.catalogue.publishing import set_publication_state as sp\n"
        "    sp(1, state='active')\n"
    )
    assert _calls(src) == [("f", "set_publication_state", 3, False, "")]


def test_watched_names_follow_the_publishing_module():
    source = (
        "def publish(): ...\n"
        "def brand_new_writer(): ...\n"
        "def content_hash(): ...\n"
        "def _private(): ...\n"
        "class C:\n"
        "    def method(self): ...\n"
    )
    assert _watched(source) == {"publish", "brand_new_writer", "set_status"}
    src = "def f():\n    brand_new_writer(sub)\n"
    assert _calls(src, _watched(source)) == [("f", "brand_new_writer", 2, False, "")]


def test_every_writer_of_the_published_copy_is_watched():
    for name in (
        "publish",
        "republish",
        "set_publication_state",
        "on_status_change",
        "rebuild_from_revision",
        "apply_caught_up_status",
        "set_status",
    ):
        assert name in CALLS, name


def test_the_functions_that_are_not_writers_still_exist():
    assert NOT_WRITERS <= _public_functions((ROOT / PUBLISHING).read_text())


def test_detector_ignores_other_calls():
    assert _calls("def f():\n    publish_label(sub)\n    obj.save()\n") == []


def test_only_listed_status_actions_remain_outside_edits():
    found = {}
    for rel in GUARDED:
        for scope, name, line, owner_edit, target in _calls((ROOT / rel).read_text()):
            if (rel, scope, name, target) not in ALLOWED or owner_edit:
                found.setdefault(rel, []).append((scope, name, target, line))
    assert found == {}, (
        f"publish or status change for an edit outside edits.py: {found}"
    )


def test_every_allowed_call_still_exists():
    seen = {
        (rel, scope, name, target)
        for rel in GUARDED
        for scope, name, _line, _owner, target in _calls((ROOT / rel).read_text())
    }
    assert ALLOWED <= seen, f"stale entries: {ALLOWED - seen}"
