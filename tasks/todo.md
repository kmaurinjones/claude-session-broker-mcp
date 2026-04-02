# Task List

## Requested Change

Build a Python MCP session broker in this repository, install it in a local MCP directory, and leave it ready to test with named, group-scoped Claude Code sessions.

Remove manual inbox fallbacks. Channel delivery must be the only supported runtime path, and the broker must fail loudly when a target session is not channel-ready.
Keep the user-facing launch syntax as `CC_NAME=... CC_GROUP=... claude ...` even though Claude strips those vars before spawning the MCP subprocess.

Add a concise shareable explanation of what the broker is and how it works, and implement a fan-out orchestration tool so one session can ask multiple peers in a single call.

Sanitize tracked files for public release, remove user-specific or company-specific references, and replace the existing published git history with a clean `main` branch so older private strings are not retained on the remote.

## Plan

- [x] Create the project scaffold with AGENTS/docs/agents/tasks files.
- [x] Resolve the local Python version constraint and pin a working interpreter.
- [x] Implement the broker daemon and persistent message model.
- [x] Implement the stdio MCP server with named session tools and reply handling.
- [x] Add installation assets for a local MCP installation directory.
- [x] Run Ruff, pytest, and smoke tests.
- [x] Make channel readiness an explicit broker capability gate.
- [x] Remove the manual inbox workflow and align the channel payload with the reference server.
- [x] Add runtime regression tests for reply handling and channel notification shape.
- [x] Add a TTY-keyed launch registry so the server can recover per-tab identity after Claude strips shell env vars.
- [x] Install a zsh `claude` wrapper that records launch identity before execing the real binary.
- [x] Add an `ask_many` fan-out tool that sends one question to multiple peers concurrently and returns per-peer results without collapsing the whole call on partial timeout or delivery failure.
- [x] Update docs to explain the product and the new orchestration flow clearly enough to share with teammates.
- [x] Replace user-specific and company-specific examples, paths, and metadata with portable generic values.
- [ ] Back up the repo, replace the git history with a sanitized `main` branch, and republish the remote.

## Review

- `uv run ruff check .` passed.
- `uv run pytest` passed with 5 tests.
- Live broker health check passed.
- Live two-session smoke test passed against the running broker daemon.
- `uv run ruff check .` passes after the channel-ready patch.
- `uv run pytest` passes with 14 tests after adding runtime regression coverage.
- `uv run pytest` still passes after adding launch-registry recovery and shell integration helper coverage.
- `uv run ruff check .` passes after adding the `ask_many` fan-out path.
- `uv run pytest` passes with 18 tests after adding fan-out regression coverage.
- Direct MCP client sanity check against the installed `claude-session-broker-server` binary shows the live tool list now includes `ask_many`.
- `uv run ruff check .` passes after the public-release scrub.
- `uv run pytest` passes with 18 tests after the public-release scrub.
