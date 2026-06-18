from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.tools.repo_review import (
    GitHubRepoConfig,
    RepoReviewTool,
    _changed_lines_from_patch,
    _parse_pr_target,
    _parse_repo,
    _rrf_merge,
)
from nanobot.rag.utils import IndexedChunk, IndexedHit
from nanobot.config.schema import Config


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
    assert _parse_repo("https://github.com/test/repo.") == ("test", "repo")
    assert _parse_repo("test/repo.git") == ("test", "repo")


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
    assert _parse_pr_target("https://github.com/test/repo/pull/42") == ("test/repo", 42)
    assert _parse_pr_target("test/repo") == (None, None)


def test_changed_lines_from_patch_fallback() -> None:
    patch = "@@ -1,2 +1,3 @@\n line\n+added\n-old\n+again"

    assert _changed_lines_from_patch("src/app.py", patch) == [2, 3]


def test_rrf_merge_combines_ranked_lists() -> None:
    chunk_a = IndexedChunk("code_review", "a.py", 1, 2, "auth token", kind="text")
    chunk_b = IndexedChunk("code_review", "b.py", 1, 2, "config", kind="text")

    merged = _rrf_merge(
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

    result = await tool._local_targeted_context(
        review_query="login token",
        target_paths=[],
        max_results=5,
        include_tests=True,
    )

    assert result == "Error: target_paths is required for limited full_repo review."
