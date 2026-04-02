# Claude Session Broker MCP

Python MCP server and local broker for named, scoped session-to-session messaging between Claude Code sessions.

This project gives each session a stable identity such as `orchestrator`, `worker-a`, or `worker-b`, restricts visibility by group, and supports both fire-and-forget instructions and request/response flows.

Channel delivery is a hard requirement. Sessions are routable only after the MCP session has completed initialization and entered direct-delivery mode.

On this machine, Claude strips ad-hoc `CC_NAME` and `CC_GROUP` environment variables before spawning user-scope MCP subprocesses. The installation therefore includes a zsh launch shim that records those values by terminal TTY before execing the real Claude binary.

See:

- `AGENTS.md`
- `docs/architecture.md`
- `docs/runbook.md`
- `tasks/todo.md`

## What This Is

This is a local messaging layer for Claude Code tabs.

- Each Claude tab gets a human name such as `orchestrator` or `worker-a`.
- Each tab also gets a group such as `main` or `team-b`.
- A local broker on `127.0.0.1:7899` tracks which sessions are alive and routes messages only within the same group.
- Each Claude tab runs a user-scope MCP server that registers itself with the broker, polls for inbound traffic, and pushes arriving messages directly into Claude through `notifications/claude/channel`.
- Because Claude strips ad-hoc `CC_NAME` and `CC_GROUP` vars before spawning the MCP subprocess on this machine, a zsh launch shim records the tab identity by terminal TTY before Claude starts, and the MCP server recovers it from there.

The effect is teammate-style session orchestration:

- `list_sessions` shows which named peers are available.
- `ask_session` sends one request and waits for one reply.
- `ask_many` fans the same request out to multiple peers concurrently and returns per-peer results.
- `tell_session` sends a one-way note.
- `reply_to_message` answers an incoming request.

## Test Loop

1. Start named Claude Code sessions:
   - `cd /path/to/orchestrator-repo && CC_NAME=orchestrator CC_GROUP=main claude --dangerously-skip-permissions --dangerously-load-development-channels server:session-broker`
   - `cd /path/to/worker-a-repo && CC_NAME=worker-a CC_GROUP=main claude --dangerously-skip-permissions --dangerously-load-development-channels server:session-broker`
   - `cd /path/to/worker-b-repo && CC_NAME=worker-b CC_GROUP=main claude --dangerously-skip-permissions --dangerously-load-development-channels server:session-broker`
2. The first live session auto-starts the local broker at `~/.claude-session-broker`.
3. From the orchestrator session, use the MCP tools:
   - `list_sessions`
   - `ask_session`
   - `ask_many`
   - `tell_session`
   - `reply_to_message`
4. The shell integration must be loaded in your shell startup file so `CC_NAME=... claude ...` writes the launch registry entry the MCP resolves later.

The wrapper at `scripts/cc-session` is now just a convenience alias around the same env vars.
Manual broker startup via `scripts/start-broker` is still available for debugging, but it is no longer required.

Critical detail: `--dangerously-load-development-channels server:session-broker` is mandatory for real Claude-to-Claude delivery. The broker no longer offers a manual inbox path; if direct delivery is broken, the send/request flow should fail or time out rather than degrading to polling.
