import json
import time
from types import SimpleNamespace

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.channels.websocket import WebSocketChannel


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
