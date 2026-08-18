from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "ssh_tools"


def _nested_if_locations() -> list[str]:
    locations: list[str] = []

    def walk(node: ast.AST, if_body_depth: int, path: Path) -> None:
        if isinstance(node, ast.If):
            if if_body_depth:
                locations.append(f"{path.relative_to(SOURCE_ROOT)}:{node.lineno}")
            walk(node.test, if_body_depth, path)
            for child in node.body:
                walk(child, if_body_depth + 1, path)
            for child in node.orelse:
                is_elif = isinstance(child, ast.If) and child.col_offset == node.col_offset
                walk(child, if_body_depth if is_elif else if_body_depth + 1, path)
            return

        for child in ast.iter_child_nodes(node):
            walk(child, if_body_depth, path)

    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        walk(ast.parse(path.read_text(encoding="utf-8")), 0, path)
    return locations


def test_source_has_no_nested_if_statements() -> None:
    assert _nested_if_locations() == []
