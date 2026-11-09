"""Agent code stays apart from evaluation code (M2 hard constraints, ADR-0007).

``faktur_beres.domain``, ``faktur_beres.tools`` and ``faktur_beres.llm`` do not import the
evaluation, generator, dataset, or seed packages, do not name the ``eval/`` or ``data/seed`` paths,
and never use the admin key. Checked statically (imports, string constants other than docstrings,
attribute names) and by importing the packages in a fresh interpreter.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from faktur_beres.config import REPO_ROOT

AGENT_PACKAGES = ("domain", "tools", "llm")
FORBIDDEN_IMPORTS = (
    "faktur_beres.evals",
    "faktur_beres.generator",
    "faktur_beres.dataset",
    "faktur_beres.seed",
)
FORBIDDEN_NAMES = ("odoo_admin_api_key", "admin_client", "ODOO_ADMIN_API_KEY")
PACKAGE_DIR = REPO_ROOT / "src" / "faktur_beres"


def agent_files() -> list[Path]:
    return sorted(p for pkg in AGENT_PACKAGES for p in (PACKAGE_DIR / pkg).rglob("*.py"))


def docstring_nodes(tree: ast.Module) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def imported_modules(tree: ast.Module) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names += [f"{node.module}.{a.name}" for a in node.names]
    return names


@pytest.mark.parametrize("path", agent_files(), ids=lambda p: str(p.relative_to(PACKAGE_DIR)))
def test_agent_module_is_clean(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad_imports = [
        m
        for m in imported_modules(tree)
        for f in FORBIDDEN_IMPORTS
        if m == f or m.startswith(f + ".")
    ]
    assert bad_imports == []

    docs = docstring_nodes(tree)
    bad_strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docs
        and (
            "eval/" in node.value
            or "data/seed" in node.value
            or node.value in ("eval", "seed", "skenario")
        )
    ]
    assert bad_strings == []

    bad_names = [
        name
        for node in ast.walk(tree)
        for name in (
            [node.attr]
            if isinstance(node, ast.Attribute)
            else [node.id]
            if isinstance(node, ast.Name)
            else []
        )
        if name in FORBIDDEN_NAMES
    ]
    assert bad_names == []


def test_importing_agent_code_loads_no_evaluation_code() -> None:
    modules = [
        "faktur_beres." + ".".join(p.relative_to(PACKAGE_DIR).with_suffix("").parts)
        for p in agent_files()
    ]
    modules = [m.removesuffix(".__init__") for m in modules]
    code = (
        "import importlib, sys\n"
        f"for m in {modules!r}: importlib.import_module(m)\n"
        f"bad = [m for m in sys.modules if m.startswith({FORBIDDEN_IMPORTS!r})]\n"
        "print(bad)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=REPO_ROOT
    )
    assert result.stdout.strip() == "[]"


def test_the_check_would_notice_a_violation() -> None:
    tree = ast.parse('from faktur_beres.dataset import get_dataset\nP = "eval/skenario"\n')
    assert imported_modules(tree)[0] == "faktur_beres.dataset"
