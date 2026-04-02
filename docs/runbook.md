# Runbook

## Local Setup

1. `uv python install 3.14.0`
2. `uv sync`
3. In each Claude Code tab, launch the MCP server with a unique name and group.
4. The first live session auto-starts the local broker at `~/.claude-session-broker`.
5. Load your shell startup file in any existing terminal so the `claude` wrapper function is active.

Note: python.org lists `3.14.3` as the latest stable release on 2026-04-02, but `uv` currently exposes `3.14.0` for managed macOS arm64 installs on this machine. The repo therefore pins `3.14.0` until `uv` catches up.

## Environment

- `CLAUDE_SESSION_BROKER_URL`
- `CLAUDE_SESSION_BROKER_NAME`
- `CLAUDE_SESSION_BROKER_GROUP`
- `CLAUDE_SESSION_BROKER_DESCRIPTION`
- `CLAUDE_SESSION_BROKER_POLL_SECONDS`

## Test Flow

1. Start two session servers with distinct names in the same group.
2. Call `list_sessions` from one session and verify the other appears.
3. Call `tell_session` and verify the target receives a pushed message immediately.
4. Call `ask_session` and verify the reply returns to the caller.
5. Call `ask_many` with two or more peers and verify the caller gets one result per peer.
6. Start a third session in another group and verify it is invisible.

## Claude Code UX

Use direct launch with explicit identity env vars:

```bash
source ~/.zshrc
cd /path/to/orchestrator-repo
CC_NAME=orchestrator CC_GROUP=main claude --dangerously-skip-permissions --dangerously-load-development-channels server:session-broker
```

```bash
source ~/.zshrc
cd /path/to/worker-repo
CC_NAME=worker-a CC_GROUP=main claude --dangerously-skip-permissions --dangerously-load-development-channels server:session-broker
```

Optional:

- Set `CLAUDE_SESSION_BROKER_DESCRIPTION` before launch for better discovery text.
- Use `scripts/cc-session` only as a convenience alias around the same env vars.
- Set `CLAUDE_SESSION_BROKER_DATA_DIR` if you want the auto-started broker to use a different state directory.

Important:

- The `server:session-broker` development channel flag is required.
- Sessions that have not finished MCP initialization are not valid routing targets. Sends and replies to them fail loudly.
- There is no manual inbox workflow anymore. If direct delivery is broken, fix the channel path instead of polling.
- The plain `CC_NAME=... claude ...` UX depends on the shell wrapper being sourced before launch. Claude itself strips those ad-hoc env vars before spawning user-scope MCP subprocesses.

## Install Target

Primary development repo:
- the checked-out repository root

Installed MCP path:
- your local MCP installation directory
