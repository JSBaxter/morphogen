# State — morphogen

This file is **operational truth**. What this cell currently runs,
exposes, depends on, and stores. Keep it accurate; update it in the
same PR as any change that affects what's live.

If a section is empty, leave the heading and write "Nothing yet."
The structure stays even when the content doesn't.

---

## What this cell does today

Colony broadcast field for cross-cell coordination and new-cell induction. Cells emit requests/signals; capable cells read the field and act.

Nothing live yet. The Python project at the cell root has its
toolchain bones in place (`pyproject.toml`, `src/morphogen/` package
skeleton, `tests/` smoke suite, cell-root `Dockerfile`), but the
MCP server exposes no domain tools yet — that lands across the
domain → SQLite repository → MCP server PRs that follow.

---

## What's running

Nothing yet.

The cell-root `Dockerfile` builds an image whose entrypoint is the
morphogen MCP server with HTTP transport on `0.0.0.0:8485` and the
SQLite database at `/var/morphogen/morphogen.db` (per the colony's
per-cell Dockerfile contract). The colony's `docker-compose.yml`
will run this image as a service in a later colony PR. Until then
the image only serves the toolless skeleton.

---

## Dependencies

### Build / runtime

- Python 3.11+ managed via `uv` at the cell root (`pyproject.toml`
  + `uv.lock`); package source under `src/morphogen/`, tests under
  `tests/`
- `fastmcp` for the MCP server runtime
- The bundled queue runtime under `dev-tools/queue/` brings its own
  Python project
### External

Nothing yet.

---

## Where secrets live

Secrets are **never** committed. Possible homes:

- A password manager (1Password, Bitwarden, etc.) — operator
  workstation only
- An environment variable on the deployment target
- A secret store the cell explicitly authenticates against

When the cell starts using a secret, list it here with its source
(not its value):

```
| Secret              | Source                                    |
|---------------------|-------------------------------------------|
| GITHUB_TOKEN        | Operator's gh CLI auth                    |
| ...                 | ...                                       |
```

---

## What's NOT under this cell's control

- Anything outside this repo
- Anything the operator runs on their own machine and hasn't
  committed here

---

## Drift log

When `STATE.md` doesn't match reality and the discrepancy can't be
fixed inline, log it here with a date and a tracked task ID:

```
- 2026-MM-DD — <description> — task_xxxxxx
```

(Empty until something drifts.)
