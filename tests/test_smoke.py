"""Toolchain smoke tests — does the project import and assemble?

These tests exist to prove the bootstrap is wired correctly, not to
exercise feature code. Real tests land alongside the domain layer.
"""

from __future__ import annotations

import morphogen
from morphogen.server import create_app


def test_package_version_is_set() -> None:
    assert morphogen.__version__


def test_create_app_returns_named_server() -> None:
    app = create_app(db_path=":memory:")
    assert app.name == "morphogen"
