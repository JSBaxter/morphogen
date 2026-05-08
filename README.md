# morphogen

Colony broadcast field for cross-cell coordination and new-cell induction. Cells emit requests/signals; capable cells read the field and act.

## Start here

- **Identity & principles:** `MANIFESTO.md`
- **Rules for contributing (human or agent):** `CONTRIBUTING.md`
- **How to verify a change:** `TESTING.md`
- **Current operational truth:** `STATE.md`
- **Recurring process activities:** `CEREMONIES.md`
- **How this cell was born + how to spawn a sibling:**
  `REPRODUCTION.md`
- **Release history:** `CHANGELOG.md`

Agents: `CLAUDE.md` and `AGENTS.md` both point back at
`CONTRIBUTING.md` and `MANIFESTO.md` — those are the sources of
truth.

## Active directories

- `src/morphogen/`
  The morphogen package — MCP server, domain models, SQLite
  storage. The MCP entry point is `src/morphogen/server.py`.
- `tests/`
  Pytest suite for the cell-root project. Run from the cell root
  with `uv run pytest`.
- `dev-tools/`
  Local-only tooling that runs on a developer's machine. Houses
  the bundled `queue/` MCP server, used by every agent working on
  this cell.  Also houses `agent-container/` (Docker image for running the
  agent in a bounded container).  And `agent-bot/` (GitHub App bot identity wrappers).

Plus the cell-root `Dockerfile`, which builds the morphogen MCP
server image (HTTP transport on port `8485`, DB at
`/var/morphogen/morphogen.db`) per the colony's per-cell contract.

(Add directories here as the cell grows.)

## Reproduction

This cell was scaffolded from the
[`stem-cell`](https://github.com/JSBaxter/stem-cell).
The exact template version this cell tracks is recorded in
`.copier-answers.yml`. See `REPRODUCTION.md` for how to spawn a
sibling cell or pull template updates.

## Notes

- Local secrets and build state are gitignored.
- Python sub-projects manage their own dependencies via local `uv`
  projects (`pyproject.toml` + `uv.lock`) and run via
  `uv run --directory <subproject> ...` rather than depending on
  global Python installs.
- Repo quality hooks live in `.pre-commit-config.yaml`; run
  `uvx pre-commit install` once and `uvx pre-commit run --all-files`
  before opening a PR. The same core checks
  run in `.github/workflows/quality.yml`.