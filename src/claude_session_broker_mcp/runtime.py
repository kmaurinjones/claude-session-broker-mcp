from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from mcp.types import Notification
from pydantic import BaseModel

from .broker_models import (
    ListSessionsResponse,
    MessageRecord,
    PollMessagesResponse,
    RegisterSessionRequest,
    ReplyMessageRequest,
    SendMessageRequest,
    SessionRecord,
    SessionStatus,
    SetChannelReadyRequest,
    SetStatusRequest,
)


def discover_git_root(cwd: Path) -> Path | None:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        return None
    return Path(completed.stdout.strip())


@dataclass(slots=True)
class SessionIdentity:
    session_name: str
    group_name: str
    description: str
    cwd: Path
    git_root: Path | None
    pid: int
    hostname: str
    status: SessionStatus

    def register_request(self) -> RegisterSessionRequest:
        git_root_value = None
        if self.git_root is not None:
            git_root_value = str(self.git_root)
        return RegisterSessionRequest(
            session_name=self.session_name,
            group_name=self.group_name,
            description=self.description,
            cwd=str(self.cwd),
            git_root=git_root_value,
            pid=self.pid,
            hostname=self.hostname,
            status=self.status,
            channel_ready=False,
        )


class AwaitedReply(BaseModel):
    request_message_id: str
    reply_message: MessageRecord


class FanoutTargetResult(BaseModel):
    target_name: str
    ok: bool
    request: MessageRecord | None
    reply: MessageRecord | None
    error_type: str | None
    error: str | None


class FanoutResponse(BaseModel):
    question: str
    subject: str | None
    timeout_seconds: int
    results: list[FanoutTargetResult]


class SessionRuntime:
    def __init__(self, broker_url: str, identity: SessionIdentity, poll_seconds: float) -> None:
        self.broker_url = broker_url.rstrip("/")
        self.identity = identity
        self.poll_seconds = poll_seconds
        self.client = httpx.AsyncClient(
            base_url=self.broker_url,
            timeout=httpx.Timeout(10.0, connect=3.0),
        )
        self.session_record: SessionRecord | None = None
        self.server_session: Any = None
        self._lock = asyncio.Lock()
        self._pending_replies: dict[str, MessageRecord] = {}
        self._reply_waiters: dict[str, asyncio.Event] = {}
        self._resolved_replies: dict[str, MessageRecord] = {}
        self._expected_reply_ids: set[str] = set()
        self._stopping = False

    @property
    def session_id(self) -> str:
        if self.session_record is None:
            raise RuntimeError("Session is not registered with the broker.")
        return self.session_record.session_id

    async def start(self) -> None:
        response = await self.client.post(
            "/v1/sessions/register",
            json=self.identity.register_request().model_dump(mode="json"),
        )
        response.raise_for_status()
        self.session_record = SessionRecord.model_validate(response.json())

    async def close(self) -> None:
        self._stopping = True
        if self.session_record is not None:
            try:
                await self.client.post(
                    "/v1/sessions/deregister",
                    json={"session_id": self.session_record.session_id},
                )
            except httpx.HTTPError:
                pass
        await self.client.aclose()

    def attach_server_session(self, session: Any) -> None:
        self.server_session = session

    async def list_sessions(self) -> ListSessionsResponse:
        response = await self.client.post(
            "/v1/sessions/list",
            json={"session_id": self.session_id},
        )
        response.raise_for_status()
        payload = ListSessionsResponse.model_validate(response.json())
        self.session_record = payload.current_session
        return payload

    async def set_status(self, status: SessionStatus, description: str) -> SessionRecord:
        response = await self.client.post(
            "/v1/sessions/status",
            json=SetStatusRequest(
                session_id=self.session_id,
                status=status,
                description=description,
            ).model_dump(mode="json"),
        )
        response.raise_for_status()
        self.session_record = SessionRecord.model_validate(response.json())
        return self.session_record

    async def set_channel_ready(self, channel_ready: bool) -> SessionRecord:
        response = await self.client.post(
            "/v1/sessions/channel-ready",
            json=SetChannelReadyRequest(
                session_id=self.session_id,
                channel_ready=channel_ready,
            ).model_dump(mode="json"),
        )
        response.raise_for_status()
        self.session_record = SessionRecord.model_validate(response.json())
        return self.session_record

    async def send_note(self, target_name: str, message: str, subject: str | None) -> MessageRecord:
        response = await self.client.post(
            "/v1/messages/send",
            json=SendMessageRequest(
                sender_session_id=self.session_id,
                target_name=target_name,
                kind="note",
                subject=subject,
                body=message,
            ).model_dump(mode="json"),
        )
        response.raise_for_status()
        return MessageRecord.model_validate(response.json())

    async def send_request(
        self,
        target_name: str,
        question: str,
        subject: str | None,
    ) -> MessageRecord:
        response = await self.client.post(
            "/v1/messages/send",
            json=SendMessageRequest(
                sender_session_id=self.session_id,
                target_name=target_name,
                kind="request",
                subject=subject,
                body=question,
            ).model_dump(mode="json"),
        )
        response.raise_for_status()
        request_message = MessageRecord.model_validate(response.json())
        async with self._lock:
            self._expected_reply_ids.add(request_message.message_id)
        return request_message

    async def send_reply(self, message_id: str, response_text: str) -> MessageRecord:
        response = await self.client.post(
            "/v1/messages/reply",
            json=ReplyMessageRequest(
                sender_session_id=self.session_id,
                reply_to_message_id=message_id,
                body=response_text,
            ).model_dump(mode="json"),
        )
        response.raise_for_status()
        return MessageRecord.model_validate(response.json())

    async def ask_many(
        self,
        target_names: list[str],
        question: str,
        subject: str | None,
        timeout_seconds: int,
    ) -> FanoutResponse:
        if len(target_names) == 0:
            raise ValueError("ask_many requires at least one target session name.")
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be at least 1.")

        seen_names: set[str] = set()
        for target_name in target_names:
            if target_name in seen_names:
                raise ValueError(
                    f"ask_many received duplicate target name '{target_name}'. "
                    "Pass each session name at most once."
                )
            seen_names.add(target_name)

        results = await asyncio.gather(
            *[
                self._ask_many_target(
                    target_name=target_name,
                    question=question,
                    subject=subject,
                    timeout_seconds=timeout_seconds,
                )
                for target_name in target_names
            ]
        )
        return FanoutResponse(
            question=question,
            subject=subject,
            timeout_seconds=timeout_seconds,
            results=results,
        )

    async def poll_once(self) -> list[MessageRecord]:
        response = await self.client.post(
            "/v1/messages/poll",
            json={"session_id": self.session_id, "limit": 100},
        )
        response.raise_for_status()
        payload = PollMessagesResponse.model_validate(response.json())
        for message in payload.messages:
            await self._record_incoming_message(message)
        return payload.messages

    async def poll_loop(self) -> None:
        while not self._stopping:
            try:
                await self.poll_once()
            except httpx.HTTPError:
                pass
            await asyncio.sleep(self.poll_seconds)

    async def announce_channel_ready_loop(self) -> None:
        while not self._stopping:
            if self.server_session is not None and self.server_session.client_params is not None:
                await self.set_channel_ready(channel_ready=True)
                return
            await asyncio.sleep(0.1)

    async def _record_incoming_message(self, message: MessageRecord) -> None:
        should_push = False
        async with self._lock:
            if message.kind == "reply" and message.reply_to_message_id is not None:
                request_message_id = message.reply_to_message_id
                if request_message_id in self._reply_waiters:
                    self._resolved_replies[request_message_id] = message
                    self._reply_waiters[request_message_id].set()
                    return
                if request_message_id in self._expected_reply_ids:
                    self._pending_replies[request_message_id] = message
                    return
                should_push = True
            else:
                should_push = True
        if should_push:
            await self.push_message(message)

    async def wait_for_reply(self, request_message_id: str, timeout_seconds: int) -> AwaitedReply:
        async with self._lock:
            if request_message_id in self._pending_replies:
                reply_message = self._pending_replies.pop(request_message_id)
                self._expected_reply_ids.discard(request_message_id)
                return AwaitedReply(
                    request_message_id=request_message_id,
                    reply_message=reply_message,
                )
            waiter = asyncio.Event()
            self._reply_waiters[request_message_id] = waiter

        try:
            await asyncio.wait_for(waiter.wait(), timeout=timeout_seconds)
        finally:
            async with self._lock:
                if request_message_id in self._reply_waiters:
                    del self._reply_waiters[request_message_id]
                if request_message_id not in self._resolved_replies:
                    self._expected_reply_ids.discard(request_message_id)

        async with self._lock:
            if request_message_id not in self._resolved_replies:
                raise TimeoutError(
                    "Timed out waiting for a reply to message "
                    f"'{request_message_id}' after {timeout_seconds} seconds."
                )
            reply_message = self._resolved_replies.pop(request_message_id)
            self._expected_reply_ids.discard(request_message_id)
            return AwaitedReply(request_message_id=request_message_id, reply_message=reply_message)

    async def _ask_many_target(
        self,
        target_name: str,
        question: str,
        subject: str | None,
        timeout_seconds: int,
    ) -> FanoutTargetResult:
        request_message: MessageRecord | None = None
        try:
            request_message = await self.send_request(
                target_name=target_name,
                question=question,
                subject=subject,
            )
            awaited_reply = await self.wait_for_reply(
                request_message_id=request_message.message_id,
                timeout_seconds=timeout_seconds,
            )
            return FanoutTargetResult(
                target_name=target_name,
                ok=True,
                request=request_message,
                reply=awaited_reply.reply_message,
                error_type=None,
                error=None,
            )
        except Exception as error:
            return FanoutTargetResult(
                target_name=target_name,
                ok=False,
                request=request_message,
                reply=None,
                error_type=type(error).__name__,
                error=str(error),
            )

    async def push_message(self, message: MessageRecord) -> None:
        if self.server_session is None:
            raise RuntimeError("Cannot push a peer message before the server session exists.")
        params = await self.channel_params(message)
        notification = Notification[dict[str, Any], str](
            method="notifications/claude/channel",
            params=params,
        )
        await self.server_session.send_notification(notification)

    async def channel_params(self, message: MessageRecord) -> dict[str, Any]:
        sender_cwd = ""
        sender_summary = ""
        sessions = await self.list_sessions()
        for peer in sessions.peers:
            if peer.session_id == message.sender_session_id:
                sender_cwd = peer.cwd
                sender_summary = peer.description
                break

        meta: dict[str, Any] = {
            "from_id": message.sender_session_id,
            "from_name": message.sender_name,
            "from_summary": sender_summary,
            "from_cwd": sender_cwd,
            "message_id": message.message_id,
            "kind": message.kind,
            "sent_at": message.created_at,
        }
        if message.reply_to_message_id is not None:
            meta["reply_to_message_id"] = message.reply_to_message_id
        return {
            "content": message.body,
            "meta": meta,
        }

    def identity_payload(self) -> dict[str, Any]:
        if self.session_record is None:
            raise RuntimeError("Session is not registered with the broker.")
        payload = self.session_record.model_dump(mode="json")
        payload["channel_push_enabled"] = (
            self.server_session is not None and self.server_session.client_params is not None
        )
        return payload
