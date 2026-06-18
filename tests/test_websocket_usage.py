import json
import time
from types import SimpleNamespace

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
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
    assert body["usage"]["total_tokens"] == 150
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
        "total_tokens": 140,
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
            "webui": True,
        },
    )

    session = manager.get_or_create("websocket:chat")
    assert session.metadata["review_mode"] is True
    assert session.metadata["review_target"] == "https://github.com/test/repo"
    assert session.metadata["review_target_type"] == "github"
    assert session.metadata["review_mode_variant"] == "deep"
    assert handled[0]["content"] == "请审查登录逻辑"
    assert handled[0]["metadata"]["review_target"] == "https://github.com/test/repo"
    assert handled[0]["metadata"]["review_target_type"] == "github"
    assert handled[0]["metadata"]["review_mode_variant"] == "deep"


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
    assert handled[0]["content"] == ""
    assert handled[0]["metadata"]["review_target"] == "./repo"
