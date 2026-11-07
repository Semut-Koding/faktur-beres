"""Static index of ``@pytest.mark.aturan("BR-xx")`` marks in the test files.

Read with ``ast`` instead of from the running session, so the traceability test gives the same
answer whichever subset of tests is selected.
"""

import ast
from collections import defaultdict
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _aturan_ids(node: ast.AST) -> list[str]:
    """BR ids in an expression such as ``pytest.mark.aturan("BR-10")`` or a list of marks."""
    found = []
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "aturan"
        ):
            found += [a.value for a in sub.args if isinstance(a, ast.Constant)]
    return found


def rule_tests() -> dict[str, list[str]]:
    """``BR-xx -> ["file::test", ...]`` for every marked test function."""
    index: dict[str, list[str]] = defaultdict(list)
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_ids: list[str] = []
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets
            ):
                module_ids += _aturan_ids(stmt.value)
        for stmt in tree.body:
            if isinstance(stmt, ast.FunctionDef) and stmt.name.startswith("test_"):
                ids = set(module_ids)
                for decorator in stmt.decorator_list:
                    ids.update(_aturan_ids(decorator))
                for rule in sorted(ids):
                    index[rule].append(f"{path.name}::{stmt.name}")
    return dict(index)
