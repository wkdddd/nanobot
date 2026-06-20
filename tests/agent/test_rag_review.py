from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger

from nanobot.agent.review.evidence import ReviewEvidenceService
from nanobot.agent.review.utils import (
    changed_lines_from_patch,
    parse_pr_target,
    parse_repo,
)
from nanobot.rag.review_service import (
    RepositoryRAGRequest,
    RepositoryRAGOptions,
    RepositoryRAGService,
    rrf_merge,
)
from nanobot.rag.utils import IndexedChunk, IndexedHit


class _GitHub:
    def __init__(self, *, enable: bool = True) -> None:
        self.config = type("GitHubConfig", (), {"enable": enable, "max_index_files": 400})()


class _LogSink:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def write(self, message: str) -> None:
        self.messages.append(message)

    @property
    def text(self) -> str:
        return "".join(self.messages)


def test_parse_github_repo_from_url_and_owner_repo() -> None:
    assert parse_repo("https://github.com/test/repo.") == ("test", "repo")
    assert parse_repo("test/repo.git") == ("test", "repo")


def test_parse_pr_target_from_url() -> None:
    assert parse_pr_target("https://github.com/test/repo/pull/42") == ("test/repo", 42)
    assert parse_pr_target("test/repo") == (None, None)


def test_changed_lines_from_patch_fallback() -> None:
    patch = "@@ -1,2 +1,3 @@\n line\n+added\n-old\n+again"

    assert changed_lines_from_patch("src/app.py", patch) == [2, 3]


def test_rrf_merge_combines_ranked_lists() -> None:
    chunk_a = IndexedChunk("code_review", "a.py", 1, 2, "auth token", kind="text")
    chunk_b = IndexedChunk("code_review", "b.py", 1, 2, "config", kind="text")

    merged = rrf_merge(
        [
            ("bm25", [IndexedHit(chunk_a, 10, ["bm25"]), IndexedHit(chunk_b, 5, ["bm25"])]),
            ("risk", [IndexedHit(chunk_b, 9, ["risk"])]),
        ],
        limit=2,
    )

    assert merged[0].chunk.path == "b.py"
    assert "risk" in merged[0].reason


@pytest.mark.asyncio
async def test_review_evidence_dispatches_local_targeted_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ReviewEvidenceService(RepositoryRAGService(tmp_path, options=RepositoryRAGOptions(enable_chonkie=False)))
    called: dict[str, object] = {}

    async def fake_local(**kwargs: object) -> str:
        called.update(kwargs)
        return "context"

    monkeypatch.setattr(service, "local_context", fake_local)

    result = await service.local_targeted_context(
        review_query="auth",
        target_paths=["src/auth.py"],
        max_results=5,
        include_tests=True,
    )

    assert result.startswith("[Limited Full Repo Review Context]")
    assert "src/auth.py" in result
    assert called["review_query"] == "auth src/auth.py"


@pytest.mark.asyncio
async def test_review_evidence_dispatches_local_changed_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ReviewEvidenceService(RepositoryRAGService(tmp_path, options=RepositoryRAGOptions(enable_chonkie=False)))
    called: dict[str, object] = {}

    async def fake_local(**kwargs: object) -> str:
        called.update(kwargs)
        return "context"

    monkeypatch.setattr(service, "local_changed_files", lambda: ["src/auth.py", "docs/readme.md"])
    monkeypatch.setattr(service, "local_context", fake_local)

    result = await service.local_changed_context(
        review_query="regression",
        target_paths=["src"],
        max_results=5,
        include_tests=True,
    )

    assert result.startswith("[Local Diff Review Context]")
    assert "src/auth.py" in result
    assert "docs/readme.md" not in result
    assert called["review_query"] == "regression src/auth.py"


@pytest.mark.asyncio
async def test_review_evidence_local_context_logs_no_hits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ReviewEvidenceService(RepositoryRAGService(tmp_path, options=RepositoryRAGOptions(enable_chonkie=False)))
    sink = _LogSink()
    handler_id = logger.add(sink, level="INFO", format="{message}")

    class _Result:
        hits: list[object] = []
        context = "No relevant repository review references found."

    async def fake_retrieve(*_args: object, **_kwargs: object) -> _Result:
        return _Result()

    monkeypatch.setattr(service.repository_rag, "retrieve", fake_retrieve)
    try:
        result = await service.local_context(
            review_query="auth",
            max_results=5,
            include_tests=True,
        )
    finally:
        logger.remove(handler_id)

    assert result == "No relevant repository review references found."
    assert "review.evidence.local.done" in sink.text
    assert "status=no_hits" in sink.text


@pytest.mark.asyncio
async def test_review_evidence_github_diff_logs_no_hits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = ReviewEvidenceService(RepositoryRAGService(tmp_path, options=RepositoryRAGOptions(enable_chonkie=False)))
    sink = _LogSink()
    handler_id = logger.add(sink, level="INFO", format="{message}")

    async def fake_fetch_pr_files(*_args: object, **_kwargs: object):
        return "test/repo#42", {"src/auth.py": "def auth(): pass"}, {"src/auth.py": [1]}

    async def fake_snapshot_context(*_args: object, **_kwargs: object):
        return tmp_path / "cache", "No relevant GitHub PR diff references found.", 0

    monkeypatch.setattr(service.github, "fetch_pr_files", fake_fetch_pr_files)
    monkeypatch.setattr(service, "retrieve_snapshot_context", fake_snapshot_context)
    try:
        result = await service.github_diff_context(
            repo="test/repo",
            pr_number=42,
            target_paths=[],
            review_query="auth",
            max_results=5,
            include_tests=True,
            trace_id="trace-1",
        )
    finally:
        logger.remove(handler_id)

    assert "No relevant GitHub PR diff references found." in result
    assert "review.evidence.github_diff.done" in sink.text
    assert "status=no_hits" in sink.text
    assert "trace_id=trace-1" in sink.text


@pytest.mark.asyncio
async def test_repository_rag_logs_empty_query_and_no_terms(tmp_path: Path) -> None:
    service = RepositoryRAGService(tmp_path, options=RepositoryRAGOptions(enable_chonkie=False))
    sink = _LogSink()
    handler_id = logger.add(sink, level="INFO", format="{message}")
    try:
        await service.retrieve(
            RepositoryRAGRequest(
                source_type="code_review",
                review_query="",
                trace_id="empty-query",
            )
        )
        hits = await service.retrieve_hits(
            source_type="code_review",
            review_query="???",
            trace_id="no-terms",
        )
    finally:
        logger.remove(handler_id)

    assert hits == []
    assert "status=empty_query" in sink.text
    assert "status=no_terms" in sink.text
