"""TimeShot's code documents itself: every module, class and function has a docstring.

Ruff's docstring rules (see pyproject.toml) check public names only; this test also holds
private functions, methods and nested functions to it, so that nobody has to guess what a
piece of code is for.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted(
    [
        *(ROOT / "src" / "timeshot").rglob("*.py"),
        *(ROOT / "packaging" / "nuitka").glob("*.py"),
        *(ROOT / "tests").glob("*.py"),
    ]
)

Documentable = ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef


def undocumented(tree: ast.Module) -> Iterator[str]:
    """Yields the module, classes and functions of ``tree`` that have no docstring.

    Args:
        tree: A parsed source file.

    Yields:
        "module" for a module without a docstring, else ``name (line N)`` for each class or
        function without one.
    """
    if ast.get_docstring(tree) is None:
        yield "module"
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and (
            ast.get_docstring(node) is None
        ):
            yield f"{node.name} (line {node.lineno})"


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: str(path.relative_to(ROOT)))
def test_everything_has_a_docstring(path: Path) -> None:
    """Every module, class and function, private and nested ones included."""
    missing = list(undocumented(ast.parse(path.read_text(encoding="utf-8"))))
    assert not missing, f"{path.name}: no docstring for " + ", ".join(missing)
