# Per-cell MCP server image for morphogen.
#
# Colony per-cell Dockerfile contract: HTTP transport, bound to
# 0.0.0.0, port 8485, SQLite database at /var/morphogen/morphogen.db.
# The colony's docker-compose runs one container per cell from this
# image and mounts a persistent volume at /var/morphogen.

FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
 && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh \
        | env UV_INSTALL_DIR=/usr/local/bin sh

WORKDIR /app

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/

RUN uv sync --frozen --no-dev

RUN mkdir -p /var/morphogen
VOLUME ["/var/morphogen"]

EXPOSE 8485

ENTRYPOINT ["uv", "run", "--no-sync", "python", "-m", "morphogen.server"]
CMD ["--transport", "http", \
     "--host", "0.0.0.0", \
     "--port", "8485", \
     "--db", "/var/morphogen/morphogen.db"]
