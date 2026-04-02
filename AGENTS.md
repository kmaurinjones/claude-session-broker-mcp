# AGENTS.md

## How To Run
- Check: `uv run ruff check .`
- Test: `uv run pytest`
- Broker: `uv run claude-session-broker-broker`
- MCP server: `uv run claude-session-broker-server`

## Architecture
- Broker daemon: `docs/architecture.md`
- Runbook and install flow: `docs/runbook.md`

## Project Records
- Task tracker: `tasks/todo.md`
- Optional local records can live in an ignored `agents/` directory if you maintain private notes outside version control.

## Constraints
- Python only. Use `uv`; no `pip`.
- Session visibility is group-scoped. Cross-group delivery is a hard failure.
- Every session must declare an explicit name and group through environment variables or CLI flags.

## Self-Improvement
- Before work, read relevant local notes if you maintain them in an ignored `agents/` directory.
- Route mistakes, corrections, and plan deviations into private local notes if you keep them.
- Route durable architectural decisions into repo docs when they matter to collaborators.
- Keep accumulated detail out of this file; put it in `agents/` or `docs/`.
