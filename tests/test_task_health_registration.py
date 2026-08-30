"""#157 Phase 1 gap: a task that reports health but is never registered.

register() seeds a row so a task appears as "never run yet" BEFORE its first
cycle. Without it a task only becomes visible once it has already succeeded
once -- so a loop that dies during startup, or never starts at all, has no row
and is invisible on the Health page.

That is a MORE silent failure than the #79 case this issue exists to prevent.
#79 at least logged a warning every cycle; a task that never reaches its first
success produces nothing, and the Health page cannot show an absence it does
not know about.

This test derives the invariant from the source rather than listing names, so
a task added later that reports without registering fails here instead of
going quietly missing.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "subarr"


def _module_string_constants(tree: ast.AST) -> dict[str, str]:
    """Module-level NAME = "literal" bindings, so a task referenced by constant
    (FORCED_SEGMENT_TASK, TASK_NAME, ...) resolves to its real name."""
    out: dict[str, str] = {}
    for node in tree.body:  # type: ignore[attr-defined]
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        out[tgt.id] = node.value.value
    return out


def _reporting_task_names() -> set[str]:
    """Every task name passed to record_success/record_failure across src."""
    names: set[str] = set()
    for py in SRC.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        consts = _module_string_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in ("record_success", "record_failure"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                names.add(first.value)
            elif isinstance(first, ast.Name) and first.id in consts:
                names.add(consts[first.id])
    return names


def _registered_task_names() -> set[str]:
    """Every name passed to task_health.register() in app.py."""
    app = SRC / "app.py"
    tree = ast.parse(app.read_text(encoding="utf-8"))
    names: set[str] = set()
    # the registration loop is `for _tname, _tiv in ( ("x", 600), ... ):`
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            for elt in node.iter.elts:
                if isinstance(elt, ast.Tuple) and elt.elts:
                    first = elt.elts[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        names.add(first.value)
        # and any direct register("name") call
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "register" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names.add(first.value)
    return names


def test_the_scanner_actually_finds_things():
    """Guard the guard.

    If either scanner silently returned an empty set the real assertion below
    would pass vacuously and this whole file would be decorative.
    """
    reporting = _reporting_task_names()
    registered = _registered_task_names()
    assert len(reporting) >= 8, f"scanner found too few reporters: {sorted(reporting)}"
    assert len(registered) >= 8, f"scanner found too few registrations: {sorted(registered)}"


def test_every_reporting_task_is_registered():
    """The invariant. A task that records health must also be seeded.

    Otherwise it is invisible until its first success, which is exactly when
    you most need to see it.
    """
    reporting = _reporting_task_names()
    registered = _registered_task_names()
    missing = sorted(reporting - registered)
    assert not missing, (
        "these tasks report health but are never register()ed, so they do not "
        f"appear on the Health page until their first success: {missing}"
    )
