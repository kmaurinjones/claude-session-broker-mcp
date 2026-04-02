from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException

from .broker_models import (
    DeregisterSessionRequest,
    HealthResponse,
    ListSessionsRequest,
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
)
from .broker_store import BrokerStore


def create_broker_app(database_path: Path, session_ttl_seconds: int) -> FastAPI:
    store = BrokerStore(database_path=database_path, session_ttl_seconds=session_ttl_seconds)
    app = FastAPI(title="Claude Session Broker", version="0.1.0")

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return store.health()

    @app.post("/v1/sessions/register", response_model=SessionRecord)
    async def register_session(request: RegisterSessionRequest) -> SessionRecord:
        try:
            return store.register_session(request)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/v1/sessions/deregister")
    async def deregister_session(request: DeregisterSessionRequest) -> dict[str, bool]:
        store.deregister_session(request)
        return {"ok": True}

    @app.post("/v1/sessions/list", response_model=ListSessionsResponse)
    async def list_sessions(request: ListSessionsRequest) -> ListSessionsResponse:
        try:
            return store.list_sessions(request.session_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/v1/sessions/status", response_model=SessionRecord)
    async def set_status(request: SetStatusRequest) -> SessionRecord:
        try:
            return store.set_status(request)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/v1/sessions/channel-ready", response_model=SessionRecord)
    async def set_channel_ready(request: SetChannelReadyRequest) -> SessionRecord:
        try:
            return store.set_channel_ready(request)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/v1/messages/send", response_model=MessageRecord)
    async def send_message(request: SendMessageRequest) -> MessageRecord:
        try:
            return store.send_message(request)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/v1/messages/reply", response_model=MessageRecord)
    async def reply_to_message(request: ReplyMessageRequest) -> MessageRecord:
        try:
            return store.reply_to_message(request)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/v1/messages/poll", response_model=PollMessagesResponse)
    async def poll_messages(request: PollMessagesRequest) -> PollMessagesResponse:
        try:
            return store.poll_messages(request)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return app
