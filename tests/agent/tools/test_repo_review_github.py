from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from loguru import logger

from nanobot.agent.review.github import GitHubRepoConfig
from nanobot.agent.review.utils import changed_lines_from_patch, parse_pr_target, parse_repo
from nanobot.agent.tools.repo_review import RepoReviewTool
from nanobot.config.schema import Config
from nanobot.rag.review import rrf_merge
from nanobot.rag.utils import IndexedChunk, IndexedHit


class _LogSink:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def write(self, message: str) -> None:
        self.messages.append(message)

    @property
    def text(self) -> str:
        return "".join(self.messages)


def test_repo_review_github_token_loads_from_tools_config() -> None:
    config = Config.model_validate(
        {
            "tools": {
                "githubRepo": {
                    "token": "ghp_config_token",
                }
            }
        }
    )

    assert config.tools.github_repo.token == "ghp_config_token"


def test_repo_review_github_api_key_alias_loads_from_tools_config() -> None:
    config = Config.model_validate(
        {
            "tools": {
                "githubRepo": {
                    "apiKey": "ghp_alias_token",
                }
            }
        }
    )

    assert config.tools.github_repo.token == "ghp_alias_token"


def test_parse_github_repo_from_url_and_owner_repo() -> None:
    assert parse_repo("https://github.com/test/repo.") == ("test", "repo")
    assert parse_repo("test/repo.git") == ("test", "repo")


@pytest.mark.asyncio
async def test_repo_review_github_requires_target_repo(tmp_path: Path) -> None:
    tool = RepoReviewTool(workspace=tmp_path)

    result = await tool.execute(target_type="github", action="full_repo")

    assert result == "Error: target_repo is required when target_type='github'."


@pytest.mark.asyncio
async def test_repo_review_github_respects_disabled_config(tmp_path: Path) -> None:
    tool = RepoReviewTool(
        workspace=tmp_path,
        github_config=GitHubRepoConfig(enable=False),
    )

    result = await tool.execute(target_type="github", action="full_repo", target_repo="test/repo")

    assert result == "Error: GitHub repository access is disabled by tools.githubRepo.enable."


@pytest.mark.asyncio
async def test_repo_review_rejects_old_actions(tmp_path: Path) -> None:
    tool = RepoReviewTool(workspace=tmp_path)

    result = await tool.execute(action="targeted_files", review_query="auth")

    assert "unknown repo_review action 'targeted_files'" in result


def test_repo_review_github_token_prefers_workspace_config_json(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        '{"tools":{"githubRepo":{"token":"workspace-token"}}}',
        encoding="utf-8",
    )
    tool = RepoReviewTool(
        workspace=tmp_path,
        github_config=GitHubRepoConfig(token="runtime-token"),
    )

    assert tool.github._workspace_config_token() == "workspace-token"


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
async def test_repo_review_full_repo_limited_scope_requires_paths_for_targeted_helper(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("def login(token):\n    return token\n", encoding="utf-8")
    tool = RepoReviewTool(workspace=tmp_path)

    result = await tool.evidence_service.local_targeted_context(
        review_query="login token",
        target_paths=[],
        max_results=5,
        include_tests=True,
    )

    assert result == "Error: target_paths is required for limited full_repo review."


@pytest.mark.asyncio
async def test_github_api_success_logs_structured_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool = RepoReviewTool(workspace=tmp_path)

    async def fake_token() -> None:
        return None

    monkeypatch.setattr(tool.github, "_get_token", fake_token)
    sink = _LogSink()
    handler_id = logger.add(sink, level="INFO", format="{message}")

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def get(self, *args, **kwargs):
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    try:
        result = await tool.github._api_get("repos/test/repo", trace_id="trace-gh")
    finally:
        logger.remove(handler_id)

    assert result == {"ok": True}
    assert "repo_review.github.api.success" in sink.text
    assert "trace_id=trace-gh" in sink.text
    assert "status=200" in sink.text
