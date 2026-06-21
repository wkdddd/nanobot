from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from loguru import logger

from nanobot.agent.review.evidence import LocalChangedSummary, ReviewEvidenceService
from nanobot.agent.review.utils import (
    changed_lines_from_patch,
    parse_pr_target,
    parse_repo,
)
from nanobot.rag.review_service import (
    RepoReviewHit,
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


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


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
    captured: dict[str, object] = {}

    class _Result:
        hits: list[object] = [object()]
        context = "context"

    async def fake_retrieve(request: RepositoryRAGRequest) -> _Result:
        captured["request"] = request
        return _Result()

    monkeypatch.setattr(
        service,
        "local_changed_summary",
        lambda: LocalChangedSummary(
            files=["src/auth.py", "docs/readme.md"],
            touched_lines={"src/auth.py": [2], "docs/readme.md": [1]},
        ),
    )
    monkeypatch.setattr(service.repository_rag, "retrieve", fake_retrieve)

    result = await service.local_changed_context(
        review_query="regression",
        target_paths=["src"],
        max_results=5,
        include_tests=True,
    )

    assert result.startswith("[Local Diff Review Context]")
    assert "src/auth.py" in result
    assert "docs/readme.md" not in result
    request = captured["request"]
    assert isinstance(request, RepositoryRAGRequest)
    assert request.review_query == "regression src/auth.py"
    assert request.touched_lines == {"src/auth.py": [2]}


def test_local_changed_summary_parses_staged_unstaged_and_untracked_lines(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "src").mkdir()
    tracked = tmp_path / "src" / "app.py"
    tracked.write_text("one\nold two\nthree\nold four\n", encoding="utf-8")
    _git(tmp_path, "add", "src/app.py")
    _git(tmp_path, "commit", "-m", "init")

    tracked.write_text("one\nnew two\nthree\nold four\n", encoding="utf-8")
    _git(tmp_path, "add", "src/app.py")
    tracked.write_text("one\nnew two\nthree\nnew four\n", encoding="utf-8")
    untracked = tmp_path / "src" / "new_file.py"
    untracked.write_text("alpha\nbeta\n", encoding="utf-8")

    service = ReviewEvidenceService(RepositoryRAGService(tmp_path, options=RepositoryRAGOptions(enable_chonkie=False)))
    summary = service.local_changed_summary()

    assert summary.files == ["src/app.py", "src/new_file.py"]
    assert summary.touched_lines["src/app.py"] == [2, 4]
    assert summary.touched_lines["src/new_file.py"] == [1, 2]


def test_repository_rag_prioritizes_chunks_overlapping_touched_lines() -> None:
    near = RepoReviewHit(path="src/app.py", score=1.0, start_line=20, end_line=30)
    overlapping = RepoReviewHit(path="src/app.py", score=1.0, start_line=3, end_line=8)
    other = RepoReviewHit(path="src/other.py", score=10.0, start_line=1, end_line=5)

    ranked = RepositoryRAGService.rank_touched_line_hits(
        [near, overlapping, other],
        {"src/app.py": [5]},
        limit=3,
    )

    assert ranked[0] is other
    assert ranked[1] is overlapping
    assert ranked[1].reason == ["diff-line-overlap", "diff-touched"]
    assert ranked[2] is near
    assert ranked[2].reason == ["diff-touched"]


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
