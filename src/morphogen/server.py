from __future__ import annotations

import argparse

from fastmcp import FastMCP


def create_app(db_path: str = "./morphogen.db") -> FastMCP:
    return FastMCP(
        name="morphogen",
        instructions=(
            "Colony broadcast field for cross-cell coordination. "
            "Cells emit requests/signals; capable cells read the field "
            "filtered by their own declared capability tags."
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the morphogen MCP server.")
    parser.add_argument("--db", default="./morphogen.db", help="SQLite database path.")
    parser.add_argument(
        "--transport",
        choices=("http", "stdio"),
        default="http",
        help="MCP transport to use.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host.")
    parser.add_argument("--port", type=int, default=8485, help="HTTP bind port.")
    args = parser.parse_args()

    app = create_app(db_path=args.db)
    if args.transport == "stdio":
        app.run(transport="stdio")
        return
    app.run(transport="http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
