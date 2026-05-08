"""Storage backends for the morphogen broadcast field.

The default backend is :class:`SQLiteRepository`. The MCP server in
``morphogen.server`` instantiates one and passes it to the
``FieldService``.
"""

from morphogen.infra.repository import SQLiteRepository

__all__ = ["SQLiteRepository"]
