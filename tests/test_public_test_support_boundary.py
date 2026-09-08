from __future__ import annotations

import ast
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent


def test_plus_tests_do_not_import_core_test_only_support() -> None:
    private_test_module = "millrace" + ".testing"
    offenders: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name for alias in node.names}
                if any(
                    name == private_test_module
                    or name.startswith(private_test_module + ".")
                    for name in imported
                ):
                    offenders.append(str(path.relative_to(TESTS_ROOT)))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == private_test_module or module.startswith(
                    private_test_module + "."
                ):
                    offenders.append(str(path.relative_to(TESTS_ROOT)))

    assert offenders == [], (
        "Plus tests must use local support with public installed Millrace APIs; "
        f"private Core test support imports found in: {sorted(set(offenders))}"
    )
