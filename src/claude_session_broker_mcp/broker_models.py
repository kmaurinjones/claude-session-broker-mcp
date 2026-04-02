from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

SessionStatus = Literal["idle", "busy", "blocked"]
MessageKind = Literal["note", "request", "reply"]

NAME_PATTERN = r"^[A-Za-z0-9._-]+$"


def epoch_seconds_now() -> int:
    return int(datetime.now(tz=UTC).timestamp())


def epoch_seconds_to_iso(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).isoformat()


class SessionRecord(BaseModel):
    session_id: str
    session_name: str = Field(min_length=1, max_length=80, pattern=NAME_PATTERN)
    group_name: str = Field(min_length=1, max_length=80, pattern=NAME_PATTERN)
    description: str
    cwd: str
    git_root: str | None
    pid: int
    hostname: str
    status: SessionStatus
    channel_ready: bool
    created_at: str
    heartbeat_at: str


class RegisterSessionRequest(BaseModel):
    session_name: str = Field(min_length=1, max_length=80, pattern=NAME_PATTERN)
    group_name: str = Field(min_length=1, max_length=80, pattern=NAME_PATTERN)
    description: str
    cwd: str = Field(min_length=1)
    git_root: str | None
    pid: int = Field(gt=0)
    hostname: str = Field(min_length=1)
    status: SessionStatus
    channel_ready: bool


class DeregisterSessionRequest(BaseModel):
    session_id: str = Field(min_length=1)


class ListSessionsRequest(BaseModel):
    session_id: str = Field(min_length=1)


class ListSessionsResponse(BaseModel):
    current_session: SessionRecord
    peers: list[SessionRecord]


class SetStatusRequest(BaseModel):
    session_id: str = Field(min_length=1)
    status: SessionStatus
    description: str


class SetChannelReadyRequest(BaseModel):
    session_id: str = Field(min_length=1)
    channel_ready: bool


class SendMessageRequest(BaseModel):
    sender_session_id: str = Field(min_length=1)
    target_name: str = Field(min_length=1, max_length=80, pattern=NAME_PATTERN)
    kind: Literal["note", "request"]
    subject: str | None
    body: str = Field(min_length=1)


class ReplyMessageRequest(BaseModel):
    sender_session_id: str = Field(min_length=1)
    reply_to_message_id: str = Field(min_length=1)
    body: str = Field(min_length=1)


class PollMessagesRequest(BaseModel):
    session_id: str = Field(min_length=1)
    limit: int = Field(ge=1, le=100)


class MessageRecord(BaseModel):
    message_id: str
    group_name: str = Field(min_length=1, max_length=80, pattern=NAME_PATTERN)
    sender_session_id: str
    sender_name: str = Field(min_length=1, max_length=80, pattern=NAME_PATTERN)
    target_session_id: str
    target_name: str = Field(min_length=1, max_length=80, pattern=NAME_PATTERN)
    kind: MessageKind
    subject: str | None
    body: str
    reply_to_message_id: str | None
    created_at: str
    delivered_at: str | None


class PollMessagesResponse(BaseModel):
    messages: list[MessageRecord]


class HealthResponse(BaseModel):
    ok: bool
    broker_time: str
    active_sessions: int
