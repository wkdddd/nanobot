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

    result = await tool.execute(target_type="github", action="meta")

    assert result == "Error: target_repo is required when target_type='github'."


@pytest.mark.asyncio
async def test_repo_review_github_respects_disabled_config(tmp_path: Path) -> None:
    tool = RepoReviewTool(
        workspace=tmp_path,
        github_config=GitHubRepoConfig(enable=False),
    )

    result = await tool.execute(target_type="github", action="meta", target_repo="test/repo")

    assert result == "Error: GitHub repository access is disabled by tools.githubRepo.enable."


@pytest.mark.asyncio
async def test_repo_review_github_delegates_to_reader(tmp_path: Path) -> None:
    tool = RepoReviewTool(workspace=tmp_path)
    seen = {}

    async def fake_execute(**kwargs):
        seen.update(kwargs)
        return "github result"

    tool.github.execute = fake_execute  # type: ignore[method-assign]

    result = await tool.execute(
        target_type="github",
        action="tree",
        target_repo="test/repo",
        tree_pattern="*.py",
        tree_limit=12,
    )

    assert result == "github result"
    assert seen == {
        "action": "tree",
        "repo": "test/repo",
        "path": None,
        "ref": None,
        "pattern": "*.py",
        "max_entries": 12,
    }


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
async def test_repo_review_evaluate_writes_markdown_report(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("def login(token):\n    return token\n", encoding="utf-8")
    dataset = tmp_path / "eval.jsonl"
    dataset.write_text(
        '{"id":"auth","question":"login token","expected_files":["src/auth.py"],"expected_symbols":["login"]}\n',
        encoding="utf-8",
    )
    tool = RepoReviewTool(workspace=tmp_path)

    result = await tool.execute(action="evaluate", dataset_path=str(dataset), max_results=3)

    assert "Evaluation report written:" in result
    assert "Average evidence coverage" in result
    assert (tmp_path / ".nanobot" / "review_reports" / "code-review-rag-eval.md").is_file()
