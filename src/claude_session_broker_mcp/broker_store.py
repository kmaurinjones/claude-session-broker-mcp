from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from .broker_models import (
    DeregisterSessionRequest,
    HealthResponse,
    ListSessionsResponse,
    MessageRecord,
    PollMessagesRequest,
    PollMessagesResponse,
    RegisterSessionRequest,
    ReplyMessageRequest,
    SendMessageRequest,
    SessionRecord,
    SetChannelReadyRequest,
    SetStatusRequest,
    epoch_seconds_now,
    epoch_seconds_to_iso,
)


class BrokerStore:
    def __init__(self, database_path: Path, session_ttl_seconds: int) -> None:
        self.database_path = database_path
        self.session_ttl_seconds = session_ttl_seconds
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    session_name TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    git_root TEXT,
                    pid INTEGER NOT NULL,
                    hostname TEXT NOT NULL,
                    status TEXT NOT NULL,
                    channel_ready INTEGER NOT NULL DEFAULT 0,
                    created_at_epoch INTEGER NOT NULL,
                    heartbeat_at_epoch INTEGER NOT NULL,
                    UNIQUE(group_name, session_name)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    group_name TEXT NOT NULL,
                    sender_session_id TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    target_session_id TEXT NOT NULL,
                    target_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    subject TEXT,
                    body TEXT NOT NULL,
                    reply_to_message_id TEXT,
                    created_at_epoch INTEGER NOT NULL,
                    delivered_at_epoch INTEGER
                );
                """
            )
            columns = connection.execute("PRAGMA table_info(sessions)").fetchall()
            column_names = {row["name"] for row in columns}
            if "channel_ready" not in column_names:
                connection.execute(
                    "ALTER TABLE sessions ADD COLUMN channel_ready INTEGER NOT NULL DEFAULT 0"
                )

    def _prune_stale_sessions(self, connection: sqlite3.Connection) -> None:
        cutoff = epoch_seconds_now() - self.session_ttl_seconds
        connection.execute(
            "DELETE FROM sessions WHERE heartbeat_at_epoch < ?",
            (cutoff,),
        )

    def _row_to_session(self, row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            session_id=row["session_id"],
            session_name=row["session_name"],
            group_name=row["group_name"],
            description=row["description"],
            cwd=row["cwd"],
            git_root=row["git_root"],
            pid=row["pid"],
            hostname=row["hostname"],
            status=row["status"],
            channel_ready=bool(row["channel_ready"]),
            created_at=epoch_seconds_to_iso(row["created_at_epoch"]),
            heartbeat_at=epoch_seconds_to_iso(row["heartbeat_at_epoch"]),
        )

    def _row_to_message(self, row: sqlite3.Row) -> MessageRecord:
        delivered_at = None
        if row["delivered_at_epoch"] is not None:
            delivered_at = epoch_seconds_to_iso(row["delivered_at_epoch"])
        return MessageRecord(
            message_id=row["message_id"],
            group_name=row["group_name"],
            sender_session_id=row["sender_session_id"],
            sender_name=row["sender_name"],
            target_session_id=row["target_session_id"],
            target_name=row["target_name"],
            kind=row["kind"],
            subject=row["subject"],
            body=row["body"],
            reply_to_message_id=row["reply_to_message_id"],
            created_at=epoch_seconds_to_iso(row["created_at_epoch"]),
            delivered_at=delivered_at,
        )

    def _require_session(self, connection: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        self._prune_stale_sessions(connection)
        row = connection.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown or stale session_id '{session_id}'.")
        return row

    def _touch_session(self, connection: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        self._require_session(connection, session_id)
        heartbeat_at_epoch = epoch_seconds_now()
        connection.execute(
            "UPDATE sessions SET heartbeat_at_epoch = ? WHERE session_id = ?",
            (heartbeat_at_epoch, session_id),
        )
        refreshed = connection.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if refreshed is None:
            raise KeyError(f"Unknown or stale session_id '{session_id}'.")
        return refreshed

    def register_session(self, request: RegisterSessionRequest) -> SessionRecord:
        session_id = str(uuid.uuid4())
        now_epoch = epoch_seconds_now()
        with self._connect() as connection:
            self._prune_stale_sessions(connection)
            try:
                connection.execute(
                    """
                    INSERT INTO sessions (
                        session_id,
                        session_name,
                        group_name,
                        description,
                        cwd,
                        git_root,
                        pid,
                        hostname,
                        status,
                        channel_ready,
                        created_at_epoch,
                        heartbeat_at_epoch
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        request.session_name,
                        request.group_name,
                        request.description,
                        request.cwd,
                        request.git_root,
                        request.pid,
                        request.hostname,
                        request.status,
                        int(request.channel_ready),
                        now_epoch,
                        now_epoch,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    "Session name "
                    f"'{request.session_name}' is already active in group "
                    f"'{request.group_name}'."
                ) from error

            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "Session registration succeeded but the row could not be reloaded."
                )
            return self._row_to_session(row)

    def deregister_session(self, request: DeregisterSessionRequest) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (request.session_id,),
            )

    def list_sessions(self, session_id: str) -> ListSessionsResponse:
        with self._connect() as connection:
            current_row = self._touch_session(connection, session_id)
            peer_rows = connection.execute(
                """
                SELECT * FROM sessions
                WHERE group_name = ? AND session_id != ?
                ORDER BY session_name ASC
                """,
                (current_row["group_name"], session_id),
            ).fetchall()
            current_session = self._row_to_session(current_row)
            peers = [self._row_to_session(row) for row in peer_rows]
            return ListSessionsResponse(current_session=current_session, peers=peers)

    def set_status(self, request: SetStatusRequest) -> SessionRecord:
        with self._connect() as connection:
            self._touch_session(connection, request.session_id)
            connection.execute(
                """
                UPDATE sessions
                SET status = ?, description = ?, heartbeat_at_epoch = ?
                WHERE session_id = ?
                """,
                (
                    request.status,
                    request.description,
                    epoch_seconds_now(),
                    request.session_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (request.session_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "Status update succeeded but the session row could not be reloaded."
                )
            return self._row_to_session(row)

    def set_channel_ready(self, request: SetChannelReadyRequest) -> SessionRecord:
        with self._connect() as connection:
            self._touch_session(connection, request.session_id)
            connection.execute(
                """
                UPDATE sessions
                SET channel_ready = ?, heartbeat_at_epoch = ?
                WHERE session_id = ?
                """,
                (
                    int(request.channel_ready),
                    epoch_seconds_now(),
                    request.session_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (request.session_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "Channel readiness update succeeded but the session row "
                    "could not be reloaded."
                )
            return self._row_to_session(row)

    def send_message(self, request: SendMessageRequest) -> MessageRecord:
        message_id = str(uuid.uuid4())
        created_at_epoch = epoch_seconds_now()
        with self._connect() as connection:
            sender_row = self._touch_session(connection, request.sender_session_id)
            target_row = connection.execute(
                """
                SELECT * FROM sessions
                WHERE group_name = ? AND session_name = ?
                """,
                (sender_row["group_name"], request.target_name),
            ).fetchone()
            if target_row is None:
                raise KeyError(
                    "Target session "
                    f"'{request.target_name}' is not active in group "
                    f"'{sender_row['group_name']}'."
                )
            if target_row["session_id"] == sender_row["session_id"]:
                raise ValueError("A session cannot send a routed message to itself.")
            if not bool(target_row["channel_ready"]):
                raise ValueError(
                    "Target session "
                    f"'{request.target_name}' is active but not channel-ready. "
                    "Start that Claude Code tab with "
                    "--dangerously-load-development-channels "
                    "server:session-broker and wait for it to register."
                )

            connection.execute(
                """
                INSERT INTO messages (
                    message_id,
                    group_name,
                    sender_session_id,
                    sender_name,
                    target_session_id,
                    target_name,
                    kind,
                    subject,
                    body,
                    reply_to_message_id,
                    created_at_epoch,
                    delivered_at_epoch
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    message_id,
                    sender_row["group_name"],
                    sender_row["session_id"],
                    sender_row["session_name"],
                    target_row["session_id"],
                    target_row["session_name"],
                    request.kind,
                    request.subject,
                    request.body,
                    None,
                    created_at_epoch,
                ),
            )
            row = connection.execute(
                "SELECT * FROM messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "Message insert succeeded but the message row could not be reloaded."
                )
            return self._row_to_message(row)

    def reply_to_message(self, request: ReplyMessageRequest) -> MessageRecord:
        message_id = str(uuid.uuid4())
        created_at_epoch = epoch_seconds_now()
        with self._connect() as connection:
            sender_row = self._touch_session(connection, request.sender_session_id)
            original_row = connection.execute(
                "SELECT * FROM messages WHERE message_id = ?",
                (request.reply_to_message_id,),
            ).fetchone()
            if original_row is None:
                raise KeyError(f"Unknown message_id '{request.reply_to_message_id}'.")
            if original_row["target_session_id"] != sender_row["session_id"]:
                raise ValueError(
                    f"Message '{request.reply_to_message_id}' was not addressed "
                    f"to session '{sender_row['session_name']}'."
                )
            target_row = self._require_session(connection, original_row["sender_session_id"])
            if not bool(target_row["channel_ready"]):
                raise ValueError(
                    "Target session "
                    f"'{target_row['session_name']}' is active but not channel-ready. "
                    "Replies require direct channel delivery."
                )
            connection.execute(
                """
                INSERT INTO messages (
                    message_id,
                    group_name,
                    sender_session_id,
                    sender_name,
                    target_session_id,
                    target_name,
                    kind,
                    subject,
                    body,
                    reply_to_message_id,
                    created_at_epoch,
                    delivered_at_epoch
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    message_id,
                    sender_row["group_name"],
                    sender_row["session_id"],
                    sender_row["session_name"],
                    target_row["session_id"],
                    target_row["session_name"],
                    "reply",
                    None,
                    request.body,
                    request.reply_to_message_id,
                    created_at_epoch,
                ),
            )
            row = connection.execute(
                "SELECT * FROM messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "Reply insert succeeded but the message row could not be reloaded."
                )
            return self._row_to_message(row)

    def poll_messages(self, request: PollMessagesRequest) -> PollMessagesResponse:
        with self._connect() as connection:
            self._touch_session(connection, request.session_id)
            rows = connection.execute(
                """
                SELECT * FROM messages
                WHERE target_session_id = ? AND delivered_at_epoch IS NULL
                ORDER BY created_at_epoch ASC
                LIMIT ?
                """,
                (request.session_id, request.limit),
            ).fetchall()
            if len(rows) == 0:
                return PollMessagesResponse(messages=[])

            delivered_at_epoch = epoch_seconds_now()
            message_ids = [row["message_id"] for row in rows]
            placeholders = ", ".join("?" for _ in message_ids)
            connection.execute(
                f"UPDATE messages SET delivered_at_epoch = ? WHERE message_id IN ({placeholders})",
                (delivered_at_epoch, *message_ids),
            )
            refreshed_rows = connection.execute(
                "SELECT * FROM messages "
                f"WHERE message_id IN ({placeholders}) "
                "ORDER BY created_at_epoch ASC",
                message_ids,
            ).fetchall()
            messages = [self._row_to_message(row) for row in refreshed_rows]
            return PollMessagesResponse(messages=messages)

    def health(self) -> HealthResponse:
        with self._connect() as connection:
            self._prune_stale_sessions(connection)
            row = connection.execute("SELECT COUNT(*) AS active_sessions FROM sessions").fetchone()
            if row is None:
                raise RuntimeError("Could not count active sessions.")
            return HealthResponse(
                ok=True,
                broker_time=epoch_seconds_to_iso(epoch_seconds_now()),
                active_sessions=row["active_sessions"],
            )
