import json
import time
from types import SimpleNamespace

import pytest

from nanobot.agent.loop import AgentLoop, TurnContext
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.manager import ChannelManager
from nanobot.channels.websocket import WebSocketChannel
from nanobot.session.manager import SessionManager


def _request(path: str = "/api/usage", token: str | None = "tok") -> SimpleNamespace:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return SimpleNamespace(path=path, headers=headers)


def _json_body(response) -> dict:
    return json.loads(bytes(response.body).decode("utf-8"))


def test_websocket_usage_requires_api_token() -> None:
    channel = WebSocketChannel(
        {"enabled": True, "host": "127.0.0.1"},
        MessageBus(),
        runtime_usage=lambda: {"usage": {}, "last_usage": {}, "started_at": 1.0},
    )

    response = channel._handle_usage(_request(token=None))

    assert response.status_code == 401


def test_websocket_usage_payload_normalizes_totals_and_note() -> None:
    channel = WebSocketChannel(
        {"enabled": True, "host": "127.0.0.1"},
        MessageBus(),
        runtime_usage=lambda: {
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 30,
                "cached_tokens": 10,
                "total_tokens": 999,
            },
            "last_usage": {
                "prompt_tokens": 12,
                "completion_tokens": 3,
            },
            "started_at": 123.0,
        },
    )
    channel._api_tokens["tok"] = time.monotonic() + 60

    response = channel._handle_usage(_request())
    body = _json_body(response)

    assert response.status_code == 200
    assert body["scope"] == "process"
    assert body["usage"]["total_tokens"] == 999
    assert body["last_usage"]["total_tokens"] == 15
    assert body["note"] == "Subagent usage is not additionally included in the global total."


def test_agent_loop_accumulates_process_usage_without_double_counting_total() -> None:
    loop = AgentLoop.__new__(AgentLoop)
    loop._total_usage = {}

    loop._accumulate_total_usage({
        "prompt_tokens": 100,
        "completion_tokens": 25,
        "total_tokens": 999,
        "cached_tokens": 40,
    })
    loop._accumulate_total_usage({
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "cached_tokens": 3,
    })

    assert loop._total_usage == {
        "prompt_tokens": 110,
        "completion_tokens": 30,
        "cached_tokens": 43,
        "total_tokens": 1017,
    }


class _FakeConnection:
    remote_address = ("127.0.0.1", 12345)

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


@pytest.mark.asyncio
async def test_websocket_review_mode_stores_and_clears_target(tmp_path) -> None:
    manager = SessionManager(tmp_path)
    channel = WebSocketChannel(
        {"enabled": True, "host": "127.0.0.1"},
        MessageBus(),
        session_manager=manager,
    )
    conn = _FakeConnection()

    await channel._dispatch_envelope(
        conn,
        "client",
        {
            "type": "set_review_mode",
            "chat_id": "chat",
            "enabled": True,
            "target": "https://github.com/test/repo",
            "target_type": "github",
        },
    )

    session = manager.get_or_create("websocket:chat")
    assert session.metadata["review_mode"] is True
    assert session.metadata["review_target"] == "https://github.com/test/repo"
    assert session.metadata["review_target_type"] == "github"
    assert conn.sent[-1]["target"] == "https://github.com/test/repo"
    assert conn.sent[-1]["target_type"] == "github"

    await channel._dispatch_envelope(
        conn,
        "client",
        {"type": "set_review_mode", "chat_id": "chat", "enabled": False},
    )

    assert session.metadata["review_mode"] is False
    assert "review_target" not in session.metadata
    assert "review_target_type" not in session.metadata
    assert "target" not in conn.sent[-1]


@pytest.mark.asyncio
async def test_websocket_message_can_carry_review_target(tmp_path, monkeypatch) -> None:
    manager = SessionManager(tmp_path)
    channel = WebSocketChannel(
        {"enabled": True, "host": "127.0.0.1"},
        MessageBus(),
        session_manager=manager,
    )
    conn = _FakeConnection()
    handled: list[dict] = []

    async def fake_handle_message(**kwargs):
        handled.append(kwargs)

    monkeypatch.setattr(channel, "_handle_message", fake_handle_message)

    await channel._dispatch_envelope(
        conn,
        "client",
        {
            "type": "message",
            "chat_id": "chat",
            "content": "请审查登录逻辑",
            "review_target": "https://github.com/test/repo",
            "review_target_type": "github",
            "review_mode_variant": "deep",
            "review_action": "diff",
            "webui": True,
        },
    )

    session = manager.get_or_create("websocket:chat")
    assert session.metadata["review_mode"] is True
    assert session.metadata["review_target"] == "https://github.com/test/repo"
    assert session.metadata["review_target_type"] == "github"
    assert session.metadata["review_mode_variant"] == "deep"
    assert session.metadata["review_action"] == "diff"
    assert handled[0]["content"] == "请审查登录逻辑"
    assert handled[0]["metadata"]["review_target"] == "https://github.com/test/repo"
    assert handled[0]["metadata"]["review_target_type"] == "github"
    assert handled[0]["metadata"]["review_mode_variant"] == "deep"
    assert handled[0]["metadata"]["review_action"] == "diff"


@pytest.mark.asyncio
async def test_websocket_message_can_send_review_metadata_without_content(tmp_path, monkeypatch) -> None:
    manager = SessionManager(tmp_path)
    channel = WebSocketChannel(
        {"enabled": True, "host": "127.0.0.1"},
        MessageBus(),
        session_manager=manager,
    )
    conn = _FakeConnection()
    handled: list[dict] = []

    async def fake_handle_message(**kwargs):
        handled.append(kwargs)

    monkeypatch.setattr(channel, "_handle_message", fake_handle_message)

    await channel._dispatch_envelope(
        conn,
        "client",
        {
            "type": "message",
            "chat_id": "chat",
            "content": "",
            "review_target": "./repo",
            "review_target_type": "local",
            "review_mode_variant": "full",
            "webui": True,
        },
    )

    session = manager.get_or_create("websocket:chat")
    assert session.metadata["review_mode"] is True
    assert session.metadata["review_target"] == "./repo"
    assert session.metadata["review_target_type"] == "local"
    assert session.metadata["review_mode_variant"] == "full"
    assert handled[0]["content"] == "审查"
    assert handled[0]["metadata"]["review_target"] == "./repo"


@pytest.mark.asyncio
async def test_websocket_message_rejects_old_review_action(tmp_path, monkeypatch) -> None:
    manager = SessionManager(tmp_path)
    channel = WebSocketChannel(
        {"enabled": True, "host": "127.0.0.1"},
        MessageBus(),
        session_manager=manager,
    )
    conn = _FakeConnection()
    handled: list[dict] = []

    async def fake_handle_message(**kwargs):
        handled.append(kwargs)

    monkeypatch.setattr(channel, "_handle_message", fake_handle_message)

    await channel._dispatch_envelope(
        conn,
        "client",
        {
            "type": "message",
            "chat_id": "chat",
            "content": "review",
            "review_action": "full_repo",
        },
    )

    assert handled == []
    assert conn.sent[-1]["event"] == "error"
    assert "Unknown review action 'full_repo'" in conn.sent[-1]["detail"]


@pytest.mark.asyncio
async def test_websocket_stream_delta_includes_review_kind() -> None:
    channel = WebSocketChannel(
        {"enabled": True, "host": "127.0.0.1"},
        MessageBus(),
    )
    conn = _FakeConnection()
    channel._attach(conn, "chat")

    await channel.send_delta(
        "chat",
        "## Report",
        {"_stream_id": "s1", "_stream_kind": "review_report"},
    )
    await channel.send_delta(
        "chat",
        "",
        {"_stream_end": True, "_stream_id": "s1", "_stream_kind": "review_report"},
    )

    assert conn.sent[0]["event"] == "delta"
    assert conn.sent[0]["kind"] == "review_report"
    assert conn.sent[1]["event"] == "stream_end"
    assert conn.sent[1]["kind"] == "review_report"


def test_stream_coalescing_keeps_review_stream_kinds_separate() -> None:
    manager = ChannelManager.__new__(ChannelManager)
    manager.bus = MessageBus()
    manager.bus.outbound.put_nowait(OutboundMessage(
        channel="websocket",
        chat_id="chat",
        content="report",
        metadata={
            "_stream_delta": True,
            "_stream_id": "report",
            "_stream_kind": "review_report",
        },
    ))
    first = OutboundMessage(
        channel="websocket",
        chat_id="chat",
        content="thinking",
        metadata={
            "_stream_delta": True,
            "_stream_id": "thinking",
            "_stream_kind": "review_thinking",
        },
    )

    merged, pending = manager._coalesce_stream_deltas(first)

    assert merged.content == "thinking"
    assert merged.metadata["_stream_kind"] == "review_thinking"
    assert len(pending) == 1
    assert pending[0].metadata["_stream_kind"] == "review_report"


@pytest.mark.asyncio
async def test_review_replaced_content_streams_report_without_duplicate_message() -> None:
    class NoTools:
        def get(self, name: str):
            return None

    loop = AgentLoop.__new__(AgentLoop)
    loop.bus = MessageBus()
    loop.tools = NoTools()
    msg = SimpleNamespace(
        channel="websocket",
        chat_id="chat",
        sender_id="client",
        session_key="websocket:chat",
        metadata={
            "_wants_stream": True,
            "review_target": "repo",
        },
    )
    ctx = TurnContext(
        msg=msg,
        session_key="websocket:chat",
        state=None,  # type: ignore[arg-type]
        turn_id="turn",
        final_content="## Code Review Report: repo",
        stop_reason="stop",
        content_replaced=True,
    )

    result = await loop._state_respond(ctx)

    first = loop.bus.outbound.get_nowait()
    second = loop.bus.outbound.get_nowait()
    assert result == "ok"
    assert ctx.outbound is None
    assert first.content == "## Code Review Report: repo"
    assert first.metadata["_stream_kind"] == "review_report"
    assert first.metadata["_stream_delta"] is True
    assert second.content == ""
    assert second.metadata["_stream_kind"] == "review_report"
    assert second.metadata["_stream_end"] is True
