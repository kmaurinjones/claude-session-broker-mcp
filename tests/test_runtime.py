from __future__ import annotations

from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from claude_session_broker_mcp.broker_models import (
    ListSessionsResponse,
    MessageRecord,
    SessionRecord,
)
from claude_session_broker_mcp.runtime import AwaitedReply, SessionIdentity, SessionRuntime


def make_runtime() -> SessionRuntime:
    identity = SessionIdentity(
        session_name="controller",
        group_name="main",
        description="coordinator",
        cwd=Path("/tmp/controller"),
        git_root=Path("/tmp/controller"),
        pid=1234,
        hostname="test-host",
        status="idle",
    )
    runtime = SessionRuntime(
        broker_url="http://127.0.0.1:7899",
        identity=identity,
        poll_seconds=0.1,
    )
    runtime.session_record = SessionRecord(
        session_id="session-controller",
        session_name="controller",
        group_name="main",
        description="coordinator",
        cwd="/tmp/controller",
        git_root="/tmp/controller",
        pid=1234,
        hostname="test-host",
        status="idle",
        channel_ready=True,
        created_at="2026-04-02T19:00:00+00:00",
        heartbeat_at="2026-04-02T19:00:00+00:00",
    )
    return runtime


def make_message(kind: str, reply_to_message_id: str | None = None) -> MessageRecord:
    return MessageRecord(
        message_id="message-1",
        group_name="main",
        sender_session_id="session-worker-a",
        sender_name="worker-a",
        target_session_id="session-controller",
        target_name="controller",
        kind=kind,
        subject=None,
        body="Ping from worker-a",
        reply_to_message_id=reply_to_message_id,
        created_at="2026-04-02T19:26:21+00:00",
        delivered_at="2026-04-02T19:26:22+00:00",
    )


@pytest.mark.asyncio
async def test_reply_to_request_is_buffered_for_waiter() -> None:
    runtime = make_runtime()
    runtime._expected_reply_ids.add("request-1")
    pushed: list[MessageRecord] = []

    async def fake_push_message(self: SessionRuntime, message: MessageRecord) -> None:
        pushed.append(message)

    runtime.push_message = MethodType(fake_push_message, runtime)

    await runtime._record_incoming_message(make_message("reply", reply_to_message_id="request-1"))

    reply = await runtime.wait_for_reply("request-1", timeout_seconds=1)

    assert reply.reply_message.message_id == "message-1"
    assert pushed == []


@pytest.mark.asyncio
async def test_reply_to_note_is_pushed_immediately() -> None:
    runtime = make_runtime()
    pushed: list[MessageRecord] = []

    async def fake_push_message(self: SessionRuntime, message: MessageRecord) -> None:
        pushed.append(message)

    runtime.push_message = MethodType(fake_push_message, runtime)

    await runtime._record_incoming_message(make_message("reply", reply_to_message_id="note-1"))

    assert [message.message_id for message in pushed] == ["message-1"]


@pytest.mark.asyncio
async def test_channel_params_match_reference_style_shape() -> None:
    runtime = make_runtime()

    async def fake_list_sessions() -> ListSessionsResponse:
        return ListSessionsResponse(
            current_session=runtime.session_record,
            peers=[
                SessionRecord(
                    session_id="session-worker-a",
                    session_name="worker-a",
                    group_name="main",
                    description="working on task A",
                    cwd="/tmp/worker-a",
                    git_root="/tmp/worker-a",
                    pid=2222,
                    hostname="test-host",
                    status="busy",
                    channel_ready=True,
                    created_at="2026-04-02T19:00:00+00:00",
                    heartbeat_at="2026-04-02T19:00:00+00:00",
                )
            ],
        )

    runtime.list_sessions = fake_list_sessions

    params = await runtime.channel_params(make_message("request"))

    assert params["content"] == "Ping from worker-a"
    assert params["meta"] == {
        "from_id": "session-worker-a",
        "from_name": "worker-a",
        "from_summary": "working on task A",
        "from_cwd": "/tmp/worker-a",
        "message_id": "message-1",
        "kind": "request",
        "sent_at": "2026-04-02T19:26:21+00:00",
    }


@pytest.mark.asyncio
async def test_push_message_uses_sdk_notification_path() -> None:
    runtime = make_runtime()
    sent: list[object] = []

    async def fake_send_notification(notification: object) -> None:
        sent.append(notification)

    runtime.server_session = SimpleNamespace(
        client_params=SimpleNamespace(capabilities=SimpleNamespace(experimental=None)),
        send_notification=fake_send_notification,
    )

    async def fake_list_sessions(self: SessionRuntime) -> ListSessionsResponse:
        return ListSessionsResponse(
            current_session=runtime.session_record,
            peers=[],
        )

    runtime.list_sessions = MethodType(fake_list_sessions, runtime)

    await runtime.push_message(make_message("note"))

    assert len(sent) == 1
    notification = sent[0]
    assert notification.method == "notifications/claude/channel"
    assert notification.params["content"] == "Ping from worker-a"


@pytest.mark.asyncio
async def test_ask_many_returns_per_target_results_without_collapsing_on_timeout() -> None:
    runtime = make_runtime()

    async def fake_send_request(
        self: SessionRuntime,
        target_name: str,
        question: str,
        subject: str | None,
    ) -> MessageRecord:
        return MessageRecord(
            message_id=f"request-{target_name}",
            group_name="main",
            sender_session_id="session-controller",
            sender_name="controller",
            target_session_id=f"session-{target_name}",
            target_name=target_name,
            kind="request",
            subject=subject,
            body=question,
            reply_to_message_id=None,
            created_at="2026-04-02T19:26:21+00:00",
            delivered_at="2026-04-02T19:26:22+00:00",
        )

    async def fake_wait_for_reply(
        self: SessionRuntime,
        request_message_id: str,
        timeout_seconds: int,
    ) -> AwaitedReply:
        if request_message_id == "request-worker-b":
            raise TimeoutError("Timed out waiting for request-worker-b.")
        target_name = request_message_id.removeprefix("request-")
        return AwaitedReply(
            request_message_id=request_message_id,
            reply_message=MessageRecord(
                message_id=f"reply-{target_name}",
                group_name="main",
                sender_session_id=f"session-{target_name}",
                sender_name=target_name,
                target_session_id="session-controller",
                target_name="controller",
                kind="reply",
                subject=None,
                body=f"Reply from {target_name}",
                reply_to_message_id=request_message_id,
                created_at="2026-04-02T19:26:23+00:00",
                delivered_at="2026-04-02T19:26:23+00:00",
            ),
        )

    runtime.send_request = MethodType(fake_send_request, runtime)
    runtime.wait_for_reply = MethodType(fake_wait_for_reply, runtime)

    result = await runtime.ask_many(
        target_names=["worker-a", "worker-b"],
        question="Status check?",
        subject="fanout",
        timeout_seconds=5,
    )

    assert [entry.target_name for entry in result.results] == ["worker-a", "worker-b"]
    assert result.results[0].ok is True
    assert result.results[0].reply is not None
    assert result.results[0].reply.body == "Reply from worker-a"
    assert result.results[1].ok is False
    assert result.results[1].request is not None
    assert result.results[1].error_type == "TimeoutError"
    assert result.results[1].error == "Timed out waiting for request-worker-b."


@pytest.mark.asyncio
async def test_ask_many_rejects_duplicate_target_names() -> None:
    runtime = make_runtime()

    with pytest.raises(ValueError, match="duplicate target name 'worker-a'"):
        await runtime.ask_many(
            target_names=["worker-a", "worker-a"],
            question="Status check?",
            subject=None,
            timeout_seconds=5,
        )
