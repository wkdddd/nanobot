from __future__ import annotations

import pytest

from nanobot.agent.rag.rerank import RerankClient, create_rerank_client_from_config
from nanobot.config.schema import RerankConfig


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _AsyncClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.path: str | None = None
        self.json_body: dict | None = None

    async def post(self, path: str, *, json: dict) -> _Response:
        self.path = path
        self.json_body = json
        return _Response(self.payload)


@pytest.mark.asyncio
async def test_qwen3_rerank_uses_compatible_reranks_endpoint() -> None:
    http = _AsyncClient(
        {
            "results": [
                {"index": 1, "relevance_score": 0.7},
                {"index": 0, "relevance_score": 0.9},
            ]
        }
    )
    client = RerankClient(api_key="sk-test", model="qwen3-rerank", top_n=5)
    client._client = http

    results = await client.rerank("query", ["doc a", "doc b"], top_n=2)

    assert client._effective_base_url() == "https://dashscope.aliyuncs.com/compatible-api/v1"
    assert http.path == "/reranks"
    assert http.json_body == {
        "model": "qwen3-rerank",
        "query": "query",
        "documents": ["doc a", "doc b"],
        "top_n": 2,
        "instruct": "Given a web search query, retrieve relevant passages that answer the query.",
    }
    assert results == [(0, 0.9), (1, 0.7)]


@pytest.mark.asyncio
async def test_dashscope_text_rerank_models_use_input_and_parameters_shape() -> None:
    http = _AsyncClient(
        {"output": {"results": [{"index": 0, "relevance_score": 0.8}]}}
    )
    client = RerankClient(
        api_key="sk-test",
        model="qwen3-vl-rerank",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    client._client = http

    results = await client.rerank("query", ["doc a"], top_n=1)

    assert client._effective_base_url() == "https://dashscope.aliyuncs.com"
    assert http.path == "/api/v1/services/rerank/text-rerank/text-rerank"
    assert http.json_body == {
        "model": "qwen3-vl-rerank",
        "input": {
            "query": "query",
            "documents": ["doc a"],
        },
        "parameters": {
            "top_n": 1,
        },
    }
    assert results == [(0, 0.8)]


def test_create_rerank_client_reads_schema_field_names_and_aliases() -> None:
    config = RerankConfig(
        enable=True,
        apiKey="sk-test",
        apiBase="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen3-rerank",
        topN=3,
    )

    client = create_rerank_client_from_config(config)

    assert client is not None
    assert client.api_key == "sk-test"
    assert client.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert client.model == "qwen3-rerank"
    assert client.top_n == 3
