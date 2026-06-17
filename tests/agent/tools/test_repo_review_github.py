from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.agent.tools.repo_review import GitHubRepoConfig, RepoReviewTool, _parse_repo
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
