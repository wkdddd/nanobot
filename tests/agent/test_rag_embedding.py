from __future__ import annotations

import pytest

from nanobot.rag.config import EmbeddingConfig
from nanobot.rag.embedding import EmbeddingClient, create_embedding_client_from_config


class _Embedding:
    def __init__(self, embedding: list[float]) -> None:
        self.embedding = embedding


class _EmbeddingsAPI:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return type(
            "EmbeddingResponse",
            (),
            {"data": [_Embedding([float(i)]) for i, _ in enumerate(kwargs["input"])]},
        )()


class _AuthFailEmbeddingsAPI:
    def __init__(self) -> None:
        self.calls = 0

    async def create(self, **_kwargs):
        self.calls += 1
        error = RuntimeError("Incorrect API key provided invalid_api_key")
        error.status_code = 401
        raise error


class _OpenAIClient:
    def __init__(self) -> None:
        self.embeddings = _EmbeddingsAPI()


class _AuthFailOpenAIClient:
    def __init__(self) -> None:
        self.embeddings = _AuthFailEmbeddingsAPI()


@pytest.mark.asyncio
async def test_embed_texts_skips_blank_inputs_and_preserves_positions() -> None:
    api = _OpenAIClient()
    client = EmbeddingClient(api_key="sk-test", batch_size=10)
    client._client = api

    results = await client.embed_texts([" first ", "", " \n\t ", "second"])

    assert api.embeddings.calls[0]["input"] == ["first", "second"]
    assert api.embeddings.calls[0]["encoding_format"] == "float"
    assert results == [[0.0], None, None, [1.0]]


@pytest.mark.asyncio
async def test_embed_texts_truncates_long_inputs_before_request() -> None:
    api = _OpenAIClient()
    client = EmbeddingClient(api_key="sk-test", max_input_chars=8)
    client._client = api

    results = await client.embed_texts(["abcdefghijk"])

    assert api.embeddings.calls[0]["input"] == ["abcdefgh"]
    assert results == [[0.0]]


@pytest.mark.asyncio
async def test_embed_query_returns_none_for_blank_query_without_api_call() -> None:
    api = _OpenAIClient()
    client = EmbeddingClient(api_key="sk-test")
    client._client = api

    result = await client.embed_query("  ")

    assert result is None
    assert api.embeddings.calls == []


@pytest.mark.asyncio
async def test_auth_failure_disables_embedding_client_for_process(capsys) -> None:
    api = _AuthFailOpenAIClient()
    client = EmbeddingClient(api_key="bad-key-123456", model="bad-model", base_url="https://bad.example/v1")
    client._client = api

    first = await client.embed_texts(["first"])
    second = await client.embed_texts(["second"])

    assert first == [None]
    assert second == [None]
    assert api.embeddings.calls == 1
    stderr = capsys.readouterr().err
    assert "Embedding API authentication failed" in stderr
    assert "https://bad.example/v1" in stderr
    assert "bad-model" in stderr
    assert "bad-...3456" in stderr
    assert "bad-key-123456" not in stderr


def test_create_embedding_client_reads_api_base_alias() -> None:
    config = EmbeddingConfig(
        enable=True,
        apiKey="sk-test",
        apiBase="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="text-embedding-v2",
        maxInputChars=1024,
    )

    client = create_embedding_client_from_config(config)

    assert client is not None
    assert client.api_key == "sk-test"
    assert client.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert client.model == "text-embedding-v2"
    assert client.max_input_chars == 1024


def test_create_embedding_client_reads_snake_case_base_url() -> None:
    config = EmbeddingConfig(
        enable=True,
        apiKey="ms-test",
        baseUrl="https://api-inference.modelscope.cn/v1",
        model="Qwen/Qwen3-Embedding-8B",
    )

    client = create_embedding_client_from_config(config)

    assert client is not None
    assert client.base_url == "https://api-inference.modelscope.cn/v1"
    assert client.model == "Qwen/Qwen3-Embedding-8B"
