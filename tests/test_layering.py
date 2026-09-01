"""The dependency arrow points one way, and it is enforced rather than intended.

The whole architectural claim of this project is that the simulation core knows
nothing about its consumers. Stage 3 adds a second consumer; this test is what
stops the claim quietly becoming false.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src"
CORE = SOURCE_ROOT / "smtsim"
SERVICE = SOURCE_ROOT / "smtsim_service"

FORBIDDEN_IN_CORE = ("smtsim_service", "fastapi", "uvicorn", "psycopg", "sqlalchemy", "alembic", "pydantic")


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def core_modules() -> list[Path]:
    return sorted(CORE.glob("*.py"))


def test_the_core_has_modules_to_check() -> None:
    assert len(core_modules()) >= 7


@pytest.mark.parametrize("module", core_modules(), ids=lambda p: p.name)
def test_the_simulation_never_imports_the_service(module: Path) -> None:
    for imported in imported_modules(module):
        root = imported.split(".")[0]
        assert root not in FORBIDDEN_IN_CORE, (
            f"{module.name} imports {imported!r}. The simulation core must not depend on "
            "the service layer or on any of its dependencies -- that is the whole point "
            "of the EventSink seam."
        )


def test_the_core_runs_without_any_service_dependency_installed() -> None:
    """Importing the simulation must not drag in fastapi, psycopg or pydantic."""
    import subprocess
    import sys

    code = (
        "import sys; import smtsim; "
        "leaked = sorted(m for m in sys.modules "
        "if m.split('.')[0] in ('fastapi','psycopg','sqlalchemy','alembic','pydantic','uvicorn')); "
        "print(','.join(leaked))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)

    assert result.stdout.strip() == "", f"importing smtsim pulled in {result.stdout.strip()}"


def test_the_service_is_allowed_to_import_the_simulation() -> None:
    """The arrow points this way, and it must actually be used."""
    if not list(SERVICE.glob("*.py")):
        pytest.skip("service package not written yet")
    imports = set()
    for module in SERVICE.rglob("*.py"):
        imports |= imported_modules(module)

    assert any(name == "smtsim" or name.startswith("smtsim.") for name in imports)
