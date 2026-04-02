from __future__ import annotations

from pathlib import Path

from claude_session_broker_mcp.broker_models import (
    DeregisterSessionRequest,
    PollMessagesRequest,
    RegisterSessionRequest,
    ReplyMessageRequest,
    SendMessageRequest,
    SetChannelReadyRequest,
    SetStatusRequest,
)
from claude_session_broker_mcp.broker_store import BrokerStore


def register_session(
    store: BrokerStore,
    name: str,
    group: str,
    cwd: str,
    channel_ready: bool = True,
) -> str:
    session = store.register_session(
        RegisterSessionRequest(
            session_name=name,
            group_name=group,
            description=f"{name} description",
            cwd=cwd,
            git_root=cwd,
            pid=1234,
            hostname="test-host",
            status="idle",
            channel_ready=channel_ready,
        )
    )
    return session.session_id


def test_group_scoping_for_list_sessions(tmp_path: Path) -> None:
    store = BrokerStore(tmp_path / "broker.db", session_ttl_seconds=120)
    session_a = register_session(store, "controller", "main", "/tmp/controller")
    register_session(store, "worker-a", "main", "/tmp/worker-a")
    register_session(store, "isolated-worker", "other-team", "/tmp/isolated-worker")

    response = store.list_sessions(session_a)

    assert response.current_session.session_name == "controller"
    assert response.current_session.channel_ready is True
    assert [peer.session_name for peer in response.peers] == ["worker-a"]


def test_send_and_poll_message(tmp_path: Path) -> None:
    store = BrokerStore(tmp_path / "broker.db", session_ttl_seconds=120)
    session_a = register_session(store, "controller", "main", "/tmp/controller")
    session_b = register_session(store, "worker-a", "main", "/tmp/worker-a")

    sent = store.send_message(
        SendMessageRequest(
            sender_session_id=session_a,
            target_name="worker-a",
            kind="request",
            subject="status",
            body="What branch are you on?",
        )
    )
    assert sent.sender_name == "controller"
    assert sent.target_name == "worker-a"

    polled = store.poll_messages(PollMessagesRequest(session_id=session_b, limit=10))

    assert len(polled.messages) == 1
    assert polled.messages[0].message_id == sent.message_id
    assert polled.messages[0].delivered_at is not None


def test_reply_routes_back_to_original_sender(tmp_path: Path) -> None:
    store = BrokerStore(tmp_path / "broker.db", session_ttl_seconds=120)
    session_a = register_session(store, "controller", "main", "/tmp/controller")
    session_b = register_session(store, "worker-a", "main", "/tmp/worker-a")

    request = store.send_message(
        SendMessageRequest(
            sender_session_id=session_a,
            target_name="worker-a",
            kind="request",
            subject=None,
            body="Status?",
        )
    )
    store.poll_messages(PollMessagesRequest(session_id=session_b, limit=10))

    reply = store.reply_to_message(
        ReplyMessageRequest(
            sender_session_id=session_b,
            reply_to_message_id=request.message_id,
            body="Still working on it.",
        )
    )

    assert reply.target_session_id == session_a
    assert reply.reply_to_message_id == request.message_id


def test_duplicate_name_in_same_group_is_rejected(tmp_path: Path) -> None:
    store = BrokerStore(tmp_path / "broker.db", session_ttl_seconds=120)
    register_session(store, "controller", "main", "/tmp/controller")

    try:
        register_session(store, "controller", "main", "/tmp/other")
    except ValueError as error:
        assert "already active" in str(error)
    else:
        raise AssertionError("Expected a duplicate session name failure.")


def test_send_rejects_target_without_channel_readiness(tmp_path: Path) -> None:
    store = BrokerStore(tmp_path / "broker.db", session_ttl_seconds=120)
    session_a = register_session(store, "controller", "main", "/tmp/controller")
    register_session(
        store,
        "worker-a",
        "main",
        "/tmp/worker-a",
        channel_ready=False,
    )

    try:
        store.send_message(
            SendMessageRequest(
                sender_session_id=session_a,
                target_name="worker-a",
                kind="request",
                subject=None,
                body="Status?",
            )
        )
    except ValueError as error:
        assert "not channel-ready" in str(error)
    else:
        raise AssertionError("Expected send_message to reject non-channel-ready target.")


def test_set_channel_ready_round_trip(tmp_path: Path) -> None:
    store = BrokerStore(tmp_path / "broker.db", session_ttl_seconds=120)
    session_a = register_session(
        store,
        "controller",
        "main",
        "/tmp/controller",
        channel_ready=False,
    )

    updated = store.set_channel_ready(
        SetChannelReadyRequest(session_id=session_a, channel_ready=True)
    )

    assert updated.channel_ready is True


def test_status_update_round_trip(tmp_path: Path) -> None:
    store = BrokerStore(tmp_path / "broker.db", session_ttl_seconds=120)
    session_a = register_session(store, "controller", "main", "/tmp/controller")

    updated = store.set_status(
        SetStatusRequest(
            session_id=session_a,
            status="busy",
            description="Working on orchestration.",
        )
    )

    assert updated.status == "busy"
    assert updated.description == "Working on orchestration."

    store.deregister_session(DeregisterSessionRequest(session_id=session_a))
