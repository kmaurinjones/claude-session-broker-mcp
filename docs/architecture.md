# Architecture

## Components

1. Broker daemon
   - FastAPI app on `127.0.0.1:7899`
   - Stores sessions, messages, and delivery status in SQLite under `.session-broker/broker.db`
   - Enforces group-based visibility and delivery restrictions

2. MCP stdio server
   - One process per Claude Code session
   - Registers the session with the broker at startup
   - Exposes tools for discovery, instructions, requests, replies, and status updates
   - Polls the broker for inbound messages and surfaces them to the local session
   - Marks itself `channel_ready` after MCP initialization completes so the broker only routes to fully live sessions

3. Shell launch registry
   - Shell wrapper records `CC_NAME`, `CC_GROUP`, and `CC_DESCRIPTION` by terminal TTY in `~/.claude-session-broker/launches/`
   - Required because Claude strips those ad-hoc environment variables before spawning user-scope MCP subprocesses in this setup

## Identity Model

- `session_name`: human-assigned stable name such as `orchestrator` or `worker-a`
- `group_name`: isolation boundary such as `main` or `team-b`
- `session_id`: generated UUID for transport routing
- `channel_ready`: broker-tracked live-session bit that gates whether a session can receive routed traffic

Names are unique within a group. Multiple sessions in the same repo are distinct because they use different names.

## Message Flow

1. Session starts the MCP server with `CLAUDE_SESSION_BROKER_NAME` and `CLAUDE_SESSION_BROKER_GROUP`.
2. If those vars are absent inside the MCP subprocess, the server resolves identity from the TTY-keyed launch registry.
3. MCP server registers with the broker and receives a `session_id`.
4. `list_sessions` returns only peers from the same group.
5. `tell_session` writes a one-way message to the broker.
6. `ask_session` writes a request message and waits for a reply that references the original message.
7. `ask_many` writes one request per named target concurrently and returns per-target success or failure results instead of collapsing the entire batch on one timeout.
8. The broker rejects sends or replies targeting sessions that are not `channel_ready`.
9. Background polling reads pending messages and emits `notifications/claude/channel` messages with raw body text and flat sender metadata.

## Transport Choices

- Broker transport: local HTTP for simplicity and easy introspection
- Persistent state: SQLite for crash recovery and message replay during development
- Notification path: Claude channel notification only. Manual inbox polling is intentionally not part of the user workflow.
