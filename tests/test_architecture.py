"""Enforce the dependency rule (PRD §6.1). A violation here is a design change, not a test fix."""

import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "src" / "sensortest"

# package -> sensortest packages it may import (besides itself)
ALLOWED: dict[str, set[str]] = {
    "core": set(),
    "events": {"core"},
    "plans": {"core"},
    "steps": {"core"},
    "devices": {"core"},
    "sinks": {"core", "events"},
    "engine": {"core", "events", "plans", "steps"},
    "characterization": {"core", "devices", "steps", "engine"},  # D-050
    "ui": {"core", "events", "engine"},
    "app": {"core", "events", "plans", "steps", "devices", "sinks", "engine", "ui"},
}
QT = ("PyQt6", "pyqtgraph")
QT_ALLOWED = {"ui", "app"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _files(package: str) -> list[Path]:
    return sorted((PKG / package).rglob("*.py"))


@pytest.mark.parametrize("package", sorted(ALLOWED))
def test_package_imports_only_what_it_may(package: str) -> None:
    bad = []
    for f in _files(package):
        for name in _imports(f):
            parts = name.split(".")
            if parts[0] == "sensortest" and len(parts) > 1:
                target = parts[1]
                if target != package and target not in ALLOWED[package]:
                    bad.append(f"{f.relative_to(PKG)} imports sensortest.{target}")
            if parts[0] in QT and package not in QT_ALLOWED:
                bad.append(f"{f.relative_to(PKG)} imports {parts[0]} (Qt only in ui/app, D-043)")
    assert not bad, "\n".join(bad)


def test_every_package_is_covered() -> None:
    present = {p.name for p in PKG.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    assert present == set(ALLOWED), f"add new packages to ALLOWED: {present ^ set(ALLOWED)}"
