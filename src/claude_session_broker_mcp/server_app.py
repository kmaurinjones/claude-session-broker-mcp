from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Literal

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession
from mcp.server.stdio import stdio_server
from mcp.shared.message import SessionMessage

from .runtime import SessionRuntime


def create_server(runtime: SessionRuntime) -> FastMCP:
    mcp = FastMCP(
        name="claude-session-broker",
        instructions=(
            "Coordinate named Claude Code sessions through a local broker. "
            "Use list_sessions to see peers, tell_session for one-way instructions, "
            "ask_session for one-to-one request/reply flow, ask_many for one-to-many fan-out, "
            "reply_to_message to answer a request, "
            "For instant inbound delivery, Claude Code must be started with "
            "--dangerously-load-development-channels server:session-broker. "
            "This server does not support manual inbox polling as a user workflow. "
            "Peer sessions must be channel-ready or routed sends will fail. "
            "When you receive an inbound session-broker channel message, pause and "
            "respond immediately. Read from_name, from_summary, from_cwd, message_id, "
            "kind, and reply_to_message_id from the channel metadata. If kind is "
            "\"request\", call reply_to_message immediately with the provided message_id. "
            "If kind is \"note\" and a response is useful, answer with "
            "tell_session(name=from_name, ...)."
        ),
    )

    @mcp.tool(description="Return this session's broker identity and routing information.")
    async def get_identity() -> dict[str, object]:
        return runtime.identity_payload()

    @mcp.tool(description="List active peer sessions in the same group.")
    async def list_sessions() -> dict[str, object]:
        result = await runtime.list_sessions()
        return result.model_dump(mode="json")

    @mcp.tool(description="Send a one-way message to another named session in the same group.")
    async def tell_session(
        name: str,
        message: str,
        ctx: Context,
        subject: str | None = None,
    ) -> dict[str, object]:
        sent = await runtime.send_note(target_name=name, message=message, subject=subject)
        await ctx.info(f"Sent note to {name}.")
        return sent.model_dump(mode="json")

    @mcp.tool(description="Ask a named session a question and wait for its reply.")
    async def ask_session(
        name: str,
        question: str,
        ctx: Context,
        timeout_seconds: int = 180,
        subject: str | None = None,
    ) -> dict[str, object]:
        request_message = await runtime.send_request(
            target_name=name,
            question=question,
            subject=subject,
        )
        await ctx.info(
            f"Asked {name}. Waiting up to {timeout_seconds} seconds for reply "
            f"to {request_message.message_id}."
        )
        reply = await runtime.wait_for_reply(
            request_message_id=request_message.message_id,
            timeout_seconds=timeout_seconds,
        )
        return {
            "request": request_message.model_dump(mode="json"),
            "reply": reply.reply_message.model_dump(mode="json"),
        }

    @mcp.tool(
        description=(
            "Ask multiple named sessions the same question concurrently and return "
            "per-session success or failure results."
        )
    )
    async def ask_many(
        names: list[str],
        question: str,
        ctx: Context,
        timeout_seconds: int = 180,
        subject: str | None = None,
    ) -> dict[str, object]:
        result = await runtime.ask_many(
            target_names=names,
            question=question,
            subject=subject,
            timeout_seconds=timeout_seconds,
        )
        successful_results = 0
        for target_result in result.results:
            if target_result.ok:
                successful_results += 1
        await ctx.info(
            f"Asked {len(names)} sessions. Received {successful_results} reply "
            f"result(s) within {timeout_seconds} seconds."
        )
        return result.model_dump(mode="json")

    @mcp.tool(description="Reply to an incoming request message by message_id.")
    async def reply_to_message(
        message_id: str,
        response: str,
        ctx: Context,
    ) -> dict[str, object]:
        sent = await runtime.send_reply(message_id=message_id, response_text=response)
        await ctx.info(f"Sent reply for {message_id}.")
        return sent.model_dump(mode="json")

    @mcp.tool(description="Update this session's availability and description.")
    async def set_status(
        status: Literal["idle", "busy", "blocked"],
        description: str,
        ctx: Context,
    ) -> dict[str, object]:
        updated = await runtime.set_status(status=status, description=description)
        await ctx.info(f"Updated status to {status}.")
        return updated.model_dump(mode="json")

    return mcp


def create_disabled_server(missing_environment_variables: list[str]) -> FastMCP:
    mcp = FastMCP(
        name="claude-session-broker",
        instructions=(
            "This session broker server is installed, but this Claude Code tab "
            "was not launched with session identity environment variables."
        ),
    )

    @mcp.tool(description="Explain how to enable the session broker for this Claude Code tab.")
    async def session_broker_status() -> dict[str, object]:
        return {
            "enabled": False,
            "missing_environment_variables": missing_environment_variables,
            "example": (
                "cd /path/to/project && "
                "CC_NAME=worker-a CC_GROUP=main claude "
                "--dangerously-skip-permissions "
                "--dangerously-load-development-channels server:session-broker"
            ),
        }

    return mcp


async def run_server_stdio(mcp: FastMCP, runtime: SessionRuntime) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await _run_with_attached_session(
            mcp=mcp,
            runtime=runtime,
            read_stream=read_stream,
            write_stream=write_stream,
        )


async def _run_with_attached_session(
    mcp: FastMCP,
    runtime: SessionRuntime,
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception],
    write_stream: MemoryObjectSendStream[SessionMessage],
) -> None:
    server = mcp._mcp_server
    initialization_options = server.create_initialization_options(
        experimental_capabilities={"claude/channel": {}},
    )

    async with anyio.create_task_group() as task_group:
        async with AsyncExitStack() as stack:
            lifespan_context = await stack.enter_async_context(server.lifespan(server))
            session = await stack.enter_async_context(
                ServerSession(
                    read_stream,
                    write_stream,
                    initialization_options,
                )
            )
            runtime.attach_server_session(session)
            await runtime.start()
            stack.push_async_callback(runtime.close)
            task_group.start_soon(runtime.announce_channel_ready_loop)
            task_group.start_soon(runtime.poll_loop)
            try:
                async for message in session.incoming_messages:
                    task_group.start_soon(
                        server._handle_message,
                        message,
                        session,
                        lifespan_context,
                        False,
                    )
            finally:
                task_group.cancel_scope.cancel()
