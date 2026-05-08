# Handoff — implement morphogen

> **This file is transient.** It briefs the next agent that picks up the morphogen implementation. Delete it as part of the same PR that lands the first real feature. Once `STATE.md` reflects "morphogen service running", this doc rots.

You're inheriting **morphogen**, a fresh agent cell. Purpose: the colony's broadcast field for cross-cell coordination — cells emit `request`s (singleton-claim, lease lifecycle) and `signal`s (open, fan-out, timer-decay), other cells read the field filtered by their own capability tags. Full spec is at [`SPEC.md`](./SPEC.md). **Read it first.**

This cell consumes [`atlas`](https://github.com/JSBaxter/atlas) at the *vocabulary* level (capability tags, optional payload schemas, induction lineage) but does **not** call atlas at runtime — capability matching happens in morphogen's own storage at read time, with the reader cell passing in its own declared tags.

## What's already been done

- Cell scaffolded from [`stem-cell`](https://github.com/JSBaxter/stem-cell) at the latest template HEAD via `gh:JSBaxter/stem-cell` URL form (`.copier-answers.yml` is canonical).
- Pushed to GitHub: https://github.com/JSBaxter/morphogen (public, `main` tracks `origin/main`).
- Queue verified: `uv sync --directory dev-tools/queue && uv run --directory dev-tools/queue pytest` → 85/85 pass.
- Python toolchain pre-wired by template: `.pre-commit-config.yaml`, `.yamllint.yml`, `.github/workflows/` for CI, ruff + mypy configs.
- **Bot identity scaffolding present** at `dev-tools/agent-bot/`, wired to colony-shared GitHub App `jb-colony-bot`. Operator credentials live at `~/.config/colony-bot/`.
- **Agent container scaffolding present** at `dev-tools/agent-container/`. Build via `docker build dev-tools/agent-container/`. Operator runs this cell's agent via either `dev-tools/agent-container/run.sh` (one-off) or the colony's `docker-compose.agents.yml` (long-lived `claude remote-control` session reachable from the Claude mobile app).
- **Branch protection on `main`**: requires PR + 1 approving review (operator approves bot's PRs); dismisses stale reviews on push; no force pushes; no deletions.
- Commits on `main`: initial scaffold, this handoff + SPEC.md import.

## Decisions already made

The full design is locked in [`SPEC.md`](./SPEC.md). Highlights:

- **Two flows, shared envelope**: `emit_request` (singleton, claim-then-decay, lease lifecycle) and `emit_signal` (open, timer-decay, fan-out). Distinct verbs at API layer; one underlying schema.
- **Concentration dedup key** = `sha256(jcs(payload) || sorted_tags || kind)`. RFC 8785 JCS canonicalization, source_cell **excluded** so cross-cell emissions reinforce. Optional `nonce` field for explicit fresh-row opt-out.
- **Tag-scoped payload schemas** owned by atlas (`tags.payload_schema`) — cells fetch and normalize before emit, prevents trivial variation from fragmenting concentration.
- **New-cell induction = single-phase `signal`** with conventional response payload `type` field (`endorse | dedupe:<cell> | refine:<...> | reject | claim-spawn | spawned:<cell>`). Atlas's `cells.induced_by` unique constraint enforces single-spawn race protection.
- **No event sourcing yet** — mutate-in-place storage with status fields. Add an event log later if observability demands it.
- **MCP tool surface (~13 tools)** and **3-table SQLite schema** are spec'd in full. Implicit prefix matching at read time (both directions in the dot tree).

## Decisions still yours

1. **Cell-root Python project layout.** The template gave us ruff/mypy/CI but no `pyproject.toml` at the cell root yet. Suggested: `pyproject.toml` at root, package at `src/morphogen/`, MCP entry at `src/morphogen/server.py` mirroring the queue's `dev-tools/queue/server.py` pattern.
2. **Implementation breakdown.** One big PR or several smaller ones (domain → infra → server → MCP tools)? Several smaller is probably right — the queue cell has clean separation between `domain/`, `infra/`, and `server.py`.
3. **`.mcp.json` registration.** Currently only the queue is wired. Once the morphogen MCP server runs locally, register it alongside queue for testing.
4. **JCS library choice.** Spec specifies RFC 8785 canonicalization. Real options: `json-canon` (Python), or implement the spec inline (~50 LoC). Pick one, record rationale.
5. **Concentration index strategy.** SPEC sketches a partial unique index `WHERE status='emitted' AND nonce IS NULL`. Verify this works as expected with sqlite (partial indexes are supported but the index machinery needs the right declaration).

## Bot identity setup

This cell uses **`jb-colony-bot`**, the colony-shared GitHub App. Operator credentials are bind-mounted into the agent container at `/etc/colony-bot/` (read-only) by the colony's `docker-compose.agents.yml`, with `AGENT_BOT_CRED_DIR=/etc/colony-bot` preset. The agent-bot scripts find the credentials with no per-session setup.

For host-side runs (operator using `dev-tools/agent-container/run.sh`), the credentials at `~/.config/colony-bot/` are still found by `as-bot.sh` if the operator sets `AGENT_BOT_CRED_DIR=~/.config/colony-bot/` — but the typical pattern from this point is the containerized agent.

For every git operation that should be attributed to the bot (commits on branches, pushes, PR open/comment), use the wrapper:

```bash
dev-tools/agent-bot/as-bot.sh git commit -m "feat(...): ..."
dev-tools/agent-bot/as-bot.sh git push -u origin <branch>
dev-tools/agent-bot/as-bot.sh gh pr create --fill
```

Operator commits made *without* the wrapper keep the operator identity — that's intentional (this handoff doc, for example, was operator-committed).

## Plan

1. **Read the cell.** `CLAUDE.md` auto-loaded — follow its reading order (MANIFESTO → CONTRIBUTING → TESTING → STATE → README). Then `SPEC.md` cover-to-cover. Then skim `dev-tools/queue/` (especially `domain/`, `infra/repository.py`, `server.py`) — that's the architectural pattern to mirror.
2. **Queue handshake** via the queue MCP server (registered in `.mcp.json`):
   - `health` to verify
   - `add_idea` (title: "implement morphogen — first cut")
   - `scope_task` → `claim_task` → `open_session`
   - `add_note` recording your decisions on the open items above.
3. **Branch** per CONTRIBUTING.md naming. Suggested first branch: `feat/bootstrap-python-project` to set up `pyproject.toml`, `src/morphogen/` skeleton, and a passing `pytest` from the cell root. Keep this PR small — just the toolchain bones, no feature code. **Per-cell Dockerfile contract** (see colony's SPEC) — also include a `Dockerfile` at cell root that runs the morphogen MCP server with HTTP transport on port `8485`, bound to `0.0.0.0`, with the database path at `/var/morphogen/morphogen.db`.
4. **Subsequent branches** (one PR each, in order):
   - `feat/morphogen-domain-layer` — models (`Morphogen`, `MorphogenResponse`, value objects for tags/payload), service contracts, in-memory repository for testing, full test coverage of business logic. Concentration dedup logic lives here.
   - `feat/morphogen-sqlite-repository` — `infra/` mirroring the queue's pattern. Schema migrations. Partial unique index for concentration.
   - `feat/morphogen-mcp-server` — wire the ~13 tools as MCP, register in `.mcp.json`. HTTP transport for the server.
   - `chore/morphogen-state-md` — update `STATE.md` to reflect morphogen running locally, delete this `HANDOFF.md`.
5. **For every PR**:
   - Commits + pushes via `dev-tools/agent-bot/as-bot.sh` (so they're authored by the bot, not the operator).
   - PR description references the queue task ID.
   - Squash-merge default; bot cannot self-approve (branch protection).
6. **After opening the PR — exit.**
   - `block_task(task_id, reason="awaiting merge", blocked_on="external")`
   - `close_session(outcome="awaiting_review")`
   - **End the session.** Do NOT start the next subtask in the same session.
   - Operator merges (or, when phase 2 of the colony lands, a listener service spawns a fresh session on merge). A *new* Claude Code session is what continues — `complete_task` happens at the start of that next session, after which the next branch begins.

## Constraints

- **Don't touch `dev-tools/queue/`** — Python, self-contained, ships its own uv venv.
- **Morphogen does not call atlas at runtime.** Capability matching is reader-side: the cell calling `read_field` passes its own declared tags. Atlas is consulted by *cells/agents* (e.g. `find_capable`, `get_tag_schema`), not by morphogen.
- **No event sourcing yet** — mutate-in-place per the spec. Resist the urge.
- **If a spec choice is wrong** (you discover it during implementation), don't silently work around it. Update `SPEC.md` as part of the PR that diverges, and explain why.
- **Never `--no-verify` commits.** If a pre-commit hook fails, fix the underlying issue.

## References

- Spec: [`SPEC.md`](./SPEC.md)
- Template: https://github.com/JSBaxter/stem-cell (HEAD pinned in `.copier-answers.yml`)
- Architectural reference for the cell pattern: `dev-tools/queue/` (especially `domain/`, `infra/repository.py`, `server.py`, `WORKFLOW.md`)
- Bot identity setup: [`dev-tools/agent-bot/README.md`](./dev-tools/agent-bot/README.md)
- Sister cells in the colony:
  - [`atlas`](https://github.com/JSBaxter/atlas) — registry of cells + capability vocabulary morphogen consumes (atlas's SPEC was already updated to add `payload_schema`, `induced_by`, `find_induced_by`, `set_tag_schema` for morphogen's use). Implementation pending.
  - [`cytometer`](https://github.com/JSBaxter/cytometer) — different purpose; SvelteKit web app pointed at cell repos.
  - [`colony`](https://github.com/JSBaxter/colony) — orchestrator; runs morphogen as a colony-level service via `docker-compose.yml` (will need a service entry added in a colony PR once morphogen ships its Dockerfile).
