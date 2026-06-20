"""GitHub repository I/O for code-review evidence collection."""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from pydantic import AliasChoices, Field

from nanobot.agent.review.utils import changed_lines_from_patch, parse_repo
from nanobot.config.schema import Base
from nanobot.rag.review import DEFAULT_TEXT_EXTS

_DEFAULT_TEXT_EXTS = DEFAULT_TEXT_EXTS


class GitHubRepoConfig(Base):
    """GitHub repo reader configuration used by repo_review."""

    enable: bool = True
    token: str = Field(
        default="",
        validation_alias=AliasChoices("token", "apiKey", "api_key"),
        serialization_alias="token",
    )
    timeout: int = 30
    max_file_size: int = 1_000_000
    max_tree_entries: int = 10_000
    max_index_files: int = 400
    max_patch_files: int = 200


class GitHubRepoReader:
    """Read remote GitHub repositories through the GitHub API."""

    def __init__(self, config: GitHubRepoConfig | None = None, *, workspace: Path | None = None) -> None:
        self.config = config or GitHubRepoConfig()
        self.workspace = workspace.expanduser().resolve() if workspace else None
        self._token_cache: str | None = None

    async def execute(
        self,
        *,
        action: str,
        repo: str,
        path: str | None = None,
        ref: str | None = None,
        pattern: str | None = None,
        max_entries: int = 500,
        pr_number: int | None = None,
    ) -> str:
        try:
            owner, repo_name = parse_repo(repo)
        except ValueError as exc:
            return f"Error: {exc}"

        if action == "meta":
            return await self._action_meta(owner, repo_name)
        if action == "tree":
            return await self._action_tree(owner, repo_name, ref, pattern, max_entries)
        if action == "file":
            if not path:
                return "Error: 'path' parameter is required for GitHub file action."
            return await self._action_file(owner, repo_name, path, ref)
        if action == "diff":
            if pr_number is None:
                return "Error: pr_number is required for GitHub diff action."
            return await self._action_diff(owner, repo_name, pr_number)
        return f"Error: unknown GitHub action '{action}'. Use 'meta', 'tree', 'file', or 'diff'."

    async def _get_token(self) -> str | None:
        if self._token_cache:
            return self._token_cache
        workspace_token = self._workspace_config_token()
        token = (
            workspace_token
            or self.config.token.strip()
            or os.environ.get("GITHUB_TOKEN", "").strip()
        )
        if token:
            self._token_cache = token
            source = "workspace config.json" if workspace_token else (
                "runtime config" if self.config.token.strip() else "GITHUB_TOKEN"
            )
            logger.info("repo_review github token loaded source={}", source)
            return token
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["gh", "auth", "token"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                self._token_cache = result.stdout.strip()
                return self._token_cache
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    def _workspace_config_token(self) -> str:
        if not self.workspace:
            return ""
        config_path = self.workspace / "config.json"
        try:
            raw = config_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return ""
        candidates: list[Any] = []
        if isinstance(data, dict):
            tools = data.get("tools")
            if isinstance(tools, dict):
                github_repo = tools.get("githubRepo") or tools.get("github_repo")
                if isinstance(github_repo, dict):
                    candidates.append(github_repo)
            github_repo = data.get("githubRepo") or data.get("github_repo")
            if isinstance(github_repo, dict):
                candidates.append(github_repo)
        for item in candidates:
            for key in ("token", "apiKey", "api_key"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    async def _api_get(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        trace_id: str = "no-trace",
    ) -> dict | list | str:
        started = time.perf_counter()
        token = await self._get_token()
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "nanobot"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"https://api.github.com/{endpoint.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout) as client:
                response = await client.get(url, headers=headers, params=params or {})
        except httpx.TimeoutException:
            logger.warning(
                "repo_review.github.api.timeout trace_id={} endpoint={} timeout={}",
                trace_id,
                endpoint,
                self.config.timeout,
            )
            return f"Error: request to GitHub API timed out ({self.config.timeout}s)."
        except httpx.HTTPError as exc:
            logger.warning(
                "repo_review.github.api.http_error trace_id={} endpoint={} reason={}",
                trace_id,
                endpoint,
                exc,
            )
            return f"Error: HTTP request failed: {exc}"

        if response.status_code == 403:
            remaining = response.headers.get("X-RateLimit-Remaining", "")
            if remaining == "0":
                reset = response.headers.get("X-RateLimit-Reset", "unknown")
                auth_hint = " Set GITHUB_TOKEN for higher limits (5000 req/hr)." if not token else ""
                logger.warning(
                    "repo_review.github.api.rate_limited trace_id={} endpoint={} reset={} authenticated={}",
                    trace_id,
                    endpoint,
                    reset,
                    bool(token),
                )
                return f"Error: GitHub API rate limited. Resets at timestamp {reset}.{auth_hint}"
            logger.warning(
                "repo_review.github.api.forbidden trace_id={} endpoint={} authenticated={}",
                trace_id,
                endpoint,
                bool(token),
            )
            return "Error: access denied (403). The repo may be private; ensure GITHUB_TOKEN is set."
        if response.status_code == 404:
            logger.warning("repo_review.github.api.not_found trace_id={} endpoint={}", trace_id, endpoint)
            return "Error: repository or path not found. Check the URL and access permissions."
        if response.status_code >= 400:
            logger.warning(
                "repo_review.github.api.error trace_id={} endpoint={} status={} body={}",
                trace_id,
                endpoint,
                response.status_code,
                response.text[:200],
            )
            return f"Error: GitHub API returned {response.status_code}: {response.text[:200]}"
        logger.info(
            "repo_review.github.api.success ✅ trace_id={} endpoint={} status={} authenticated={} elapsed_ms={:.1f}",
            trace_id,
            endpoint,
            response.status_code,
            bool(token),
            (time.perf_counter() - started) * 1000,
        )
        return response.json()

    async def _action_meta(self, owner: str, repo: str) -> str:
        trace_id = "github.meta"
        started = time.perf_counter()
        data = await self._api_get(f"repos/{owner}/{repo}", trace_id=trace_id)
        if isinstance(data, str):
            return data
        lines = [
            f"Repository: {owner}/{repo}",
            f"Description: {data.get('description') or '(none)'}",
            f"Default branch: {data.get('default_branch', 'main')}",
            f"Language: {data.get('language') or 'unknown'}",
            f"Size: {data.get('size', 0)} KB",
            f"Stars: {data.get('stargazers_count', 0)}",
            f"Forks: {data.get('forks_count', 0)}",
            f"Topics: {', '.join(data.get('topics', [])) or '(none)'}",
            f"License: {(data.get('license') or {}).get('spdx_id', 'unknown')}",
            f"Visibility: {data.get('visibility', 'unknown')}",
        ]
        logger.info(
            "repo_review.github.meta.done ✅ trace_id={} repo={}/{} chars={} elapsed_ms={:.1f}",
            trace_id,
            owner,
            repo,
            len("\n".join(lines)),
            (time.perf_counter() - started) * 1000,
        )
        return "\n".join(lines)

    async def _action_tree(
        self,
        owner: str,
        repo: str,
        ref: str | None,
        pattern: str | None,
        max_entries: int,
    ) -> str:
        trace_id = "github.tree"
        started = time.perf_counter()
        if not ref:
            meta = await self._api_get(f"repos/{owner}/{repo}", trace_id=trace_id)
            if isinstance(meta, str):
                return meta
            ref = meta.get("default_branch", "main")

        data = await self._api_get(
            f"repos/{owner}/{repo}/git/trees/{ref}",
            params={"recursive": "1"},
            trace_id=trace_id,
        )
        if isinstance(data, str):
            return data

        max_entries = min(max(int(max_entries or 500), 1), self.config.max_tree_entries)
        tree = data.get("tree", [])
        truncated = data.get("truncated", False)
        entries: list[str] = []
        for item in tree:
            item_path = item.get("path", "")
            if pattern and not fnmatch.fnmatch(item_path, pattern):
                continue
            suffix = "/" if item.get("type") == "tree" else ""
            size = item.get("size")
            size_str = f"  ({size} B)" if size and item.get("type") == "blob" else ""
            entries.append(f"{item_path}{suffix}{size_str}")
            if len(entries) >= max_entries:
                entries.append(f"... (truncated at {max_entries}, total {len(tree)} entries)")
                break

        header = f"Tree for {owner}/{repo} @ {ref}"
        if pattern:
            header += f" (filter: {pattern})"
        if truncated:
            header += " [GitHub: tree was truncated due to size]"
        header += f"\n{'-' * len(header)}\n"
        result = header + "\n".join(entries) if entries else header + "(no matching entries)"
        logger.info(
            "repo_review.github.tree.done 🌳 trace_id={} repo={}/{} ref={} entries={} chars={} elapsed_ms={:.1f}",
            trace_id,
            owner,
            repo,
            ref,
            len(entries),
            len(result),
            (time.perf_counter() - started) * 1000,
        )
        return result

    async def _action_file(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str | None,
    ) -> str:
        trace_id = "github.file"
        started = time.perf_counter()
        params = {"ref": ref} if ref else {}
        data = await self._api_get(
            f"repos/{owner}/{repo}/contents/{path.lstrip('/')}",
            params,
            trace_id=trace_id,
        )
        if isinstance(data, str):
            return data

        if isinstance(data, list):
            lines = [f"Directory: {path}/"]
            for item in data[:200]:
                suffix = "/" if item.get("type") == "dir" else ""
                lines.append(f"  {item.get('name', '')}{suffix}")
            result = "\n".join(lines)
            logger.info(
                "repo_review.github.file.done 📄 trace_id={} repo={}/{} path={} kind=directory entries={} chars={} elapsed_ms={:.1f}",
                trace_id,
                owner,
                repo,
                path,
                len(lines) - 1,
                len(result),
                (time.perf_counter() - started) * 1000,
            )
            return result

        encoding = data.get("encoding", "")
        content = data.get("content", "")
        size = data.get("size", 0)

        if encoding == "base64" and content:
            try:
                decoded = base64.b64decode(content).decode("utf-8", errors="replace")
            except Exception:
                return f"Error: failed to decode file content for '{path}'."
        elif encoding == "none" or not content:
            return (
                f"File '{path}' is too large for the Contents API ({size} bytes). "
                "Consider using a smaller file or cloning the repo."
            )
        else:
            decoded = content

        if size > self.config.max_file_size:
            decoded = decoded[: self.config.max_file_size]
            truncated_note = f"\n\n[Truncated at {self.config.max_file_size} bytes, total {size}]"
        else:
            truncated_note = ""

        header = f"File: {path} ({size} bytes, sha: {data.get('sha', '?')[:8]})\n{'-' * 40}\n"
        result = header + decoded + truncated_note
        logger.info(
            "repo_review.github.file.done 📄 trace_id={} repo={}/{} path={} kind=file size={} chars={} truncated={} elapsed_ms={:.1f}",
            trace_id,
            owner,
            repo,
            path,
            size,
            len(result),
            bool(truncated_note),
            (time.perf_counter() - started) * 1000,
        )
        return result

    async def _action_diff(self, owner: str, repo: str, pr_number: int) -> str:
        trace_id = "github.diff"
        started = time.perf_counter()
        data = await self._api_get(f"repos/{owner}/{repo}/pulls/{pr_number}/files", trace_id=trace_id)
        if isinstance(data, str):
            return data
        if not isinstance(data, list):
            return "Error: unexpected GitHub PR files response."
        lines = [f"Pull Request Diff: {owner}/{repo}#{pr_number}", "-" * 40]
        for item in data[: self.config.max_patch_files]:
            filename = item.get("filename", "")
            status = item.get("status", "")
            additions = item.get("additions", 0)
            deletions = item.get("deletions", 0)
            lines.append(f"\n## {filename} ({status}, +{additions}/-{deletions})")
            patch = item.get("patch") or ""
            if patch:
                lines.append("```diff")
                lines.append(patch[:8000])
                lines.append("```")
        result = "\n".join(lines)
        logger.info(
            "repo_review.github.diff.done 🔎 trace_id={} repo={}/{} pr={} files={} chars={} elapsed_ms={:.1f}",
            trace_id,
            owner,
            repo,
            pr_number,
            min(len(data), self.config.max_patch_files),
            len(result),
            (time.perf_counter() - started) * 1000,
        )
        return result

    async def fetch_text_files(
        self,
        repo: str,
        *,
        ref: str | None = None,
        pattern: str | None = None,
        max_files: int | None = None,
        trace_id: str = "no-trace",
    ) -> tuple[str, dict[str, str]]:
        pygithub_result = await asyncio.to_thread(
            self._fetch_text_files_pygithub,
            repo,
            ref,
            pattern,
            max_files,
            trace_id,
        )
        if pygithub_result is not None:
            return pygithub_result

        owner, repo_name = parse_repo(repo)
        if not ref:
            meta = await self._api_get(f"repos/{owner}/{repo_name}", trace_id=trace_id)
            if isinstance(meta, str):
                raise RuntimeError(meta)
            ref = meta.get("default_branch", "main")
        tree_data = await self._api_get(
            f"repos/{owner}/{repo_name}/git/trees/{ref}",
            params={"recursive": "1"},
            trace_id=trace_id,
        )
        if isinstance(tree_data, str):
            raise RuntimeError(tree_data)
        files: dict[str, str] = {}
        limit = min(max_files or self.config.max_index_files, self.config.max_index_files)
        for item in tree_data.get("tree", []):
            if item.get("type") != "blob":
                continue
            path = str(item.get("path", ""))
            suffix = Path(path).suffix.lower()
            if suffix not in _DEFAULT_TEXT_EXTS:
                continue
            if pattern and not fnmatch.fnmatch(path, pattern):
                continue
            size = int(item.get("size") or 0)
            if size > self.config.max_file_size:
                continue
            content = await self._fetch_file_text(owner, repo_name, path, ref, trace_id=trace_id)
            if content is not None:
                files[path] = content
            if len(files) >= limit:
                break
        logger.info(
            "repo_review github fetched files repo={}/{} ref={} files={} limit={}",
            owner,
            repo_name,
            ref,
            len(files),
            limit,
        )
        return f"{owner}/{repo_name}@{ref}", files

    async def fetch_pr_files(
        self,
        repo: str,
        *,
        pr_number: int,
        trace_id: str = "no-trace",
    ) -> tuple[str, dict[str, str], dict[str, list[int]]]:
        pygithub_result = await asyncio.to_thread(
            self._fetch_pr_files_pygithub,
            repo,
            pr_number,
            trace_id,
        )
        if pygithub_result is not None:
            return pygithub_result

        owner, repo_name = parse_repo(repo)
        pr_data = await self._api_get(f"repos/{owner}/{repo_name}/pulls/{pr_number}", trace_id=trace_id)
        head_ref = None
        if isinstance(pr_data, dict):
            head = pr_data.get("head")
            if isinstance(head, dict):
                head_ref = head.get("sha")
        data = await self._api_get(f"repos/{owner}/{repo_name}/pulls/{pr_number}/files", trace_id=trace_id)
        if isinstance(data, str):
            raise RuntimeError(data)
        files: dict[str, str] = {}
        touched: dict[str, list[int]] = {}
        for item in data[: self.config.max_patch_files]:
            filename = str(item.get("filename", ""))
            if not filename or Path(filename).suffix.lower() not in _DEFAULT_TEXT_EXTS:
                continue
            patch = item.get("patch") or ""
            touched[filename] = changed_lines_from_patch(filename, patch)
            content = await self._fetch_file_text(
                owner,
                repo_name,
                filename,
                head_ref,
                trace_id=trace_id,
            )
            if content is None and patch:
                content = patch
            if content is not None:
                files[filename] = content
        logger.info(
            "repo_review.github.fetched_pr_files ✅ trace_id={} repo={}/{} pr={} files={} touched_files={}",
            trace_id,
            owner,
            repo_name,
            pr_number,
            len(files),
            len(touched),
        )
        return f"{owner}/{repo_name}#{pr_number}", files, touched

    def _github_client(self) -> Any | None:
        try:
            from github import Github  # type: ignore
        except Exception as exc:
            logger.debug("repo_review PyGithub unavailable reason={}", exc)
            return None
        token = self._workspace_config_token() or self.config.token.strip() or os.environ.get("GITHUB_TOKEN", "").strip()
        try:
            return Github(token or None, timeout=self.config.timeout)
        except Exception as exc:
            logger.warning("repo_review PyGithub client init failed reason={}", exc)
            return None

    def _fetch_text_files_pygithub(
        self,
        repo: str,
        ref: str | None,
        pattern: str | None,
        max_files: int | None,
        trace_id: str = "no-trace",
    ) -> tuple[str, dict[str, str]] | None:
        client = self._github_client()
        if client is None:
            return None
        try:
            owner, repo_name = parse_repo(repo)
            repo_slug = f"{owner}/{repo_name}"
            gh_repo = client.get_repo(repo_slug)
            ref_name = ref or gh_repo.default_branch
            tree = gh_repo.get_git_tree(ref_name, recursive=True).tree
            files: dict[str, str] = {}
            limit = min(max_files or self.config.max_index_files, self.config.max_index_files)
            for item in tree:
                path = str(getattr(item, "path", ""))
                if getattr(item, "type", "") != "blob":
                    continue
                if Path(path).suffix.lower() not in _DEFAULT_TEXT_EXTS:
                    continue
                if pattern and not fnmatch.fnmatch(path, pattern):
                    continue
                size = int(getattr(item, "size", 0) or 0)
                if size > self.config.max_file_size:
                    continue
                content_file = gh_repo.get_contents(path, ref=ref_name)
                if isinstance(content_file, list):
                    continue
                decoded = content_file.decoded_content.decode("utf-8", errors="replace")
                files[path] = decoded
                if len(files) >= limit:
                    break
            logger.info(
                "repo_review.github.pygithub.fetched_files ✅ trace_id={} repo={} ref={} files={}",
                trace_id,
                repo,
                ref_name,
                len(files),
            )
            return f"{repo_slug}@{ref_name}", files
        except Exception as exc:
            logger.warning("repo_review PyGithub full repo fallback repo={} reason={}", repo, exc)
            return None

    def _fetch_pr_files_pygithub(
        self,
        repo: str,
        pr_number: int,
        trace_id: str = "no-trace",
    ) -> tuple[str, dict[str, str], dict[str, list[int]]] | None:
        client = self._github_client()
        if client is None:
            return None
        try:
            owner, repo_name = parse_repo(repo)
            repo_slug = f"{owner}/{repo_name}"
            gh_repo = client.get_repo(repo_slug)
            pr = gh_repo.get_pull(pr_number)
            files: dict[str, str] = {}
            touched: dict[str, list[int]] = {}
            for item in list(pr.get_files())[: self.config.max_patch_files]:
                filename = str(getattr(item, "filename", ""))
                if not filename or Path(filename).suffix.lower() not in _DEFAULT_TEXT_EXTS:
                    continue
                patch = str(getattr(item, "patch", "") or "")
                touched[filename] = changed_lines_from_patch(filename, patch)
                try:
                    content_file = gh_repo.get_contents(filename, ref=pr.head.sha)
                    if isinstance(content_file, list):
                        continue
                    files[filename] = content_file.decoded_content.decode("utf-8", errors="replace")
                except Exception:
                    if patch:
                        files[filename] = patch
            logger.info(
                "repo_review.github.pygithub.fetched_pr_files ✅ trace_id={} repo={} pr={} files={} touched_files={}",
                trace_id,
                repo,
                pr_number,
                len(files),
                len(touched),
            )
            return f"{repo_slug}#{pr_number}", files, touched
        except Exception as exc:
            logger.warning("repo_review PyGithub PR fallback repo={} pr={} reason={}", repo, pr_number, exc)
            return None

    async def _fetch_file_text(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str | None,
        *,
        trace_id: str = "no-trace",
    ) -> str | None:
        params = {"ref": ref} if ref else {}
        data = await self._api_get(f"repos/{owner}/{repo}/contents/{path}", params, trace_id=trace_id)
        if isinstance(data, str) or isinstance(data, list):
            return None
        if int(data.get("size") or 0) > self.config.max_file_size:
            return None
        content = data.get("content", "")
        if data.get("encoding") == "base64" and content:
            try:
                return base64.b64decode(content).decode("utf-8", errors="replace")
            except Exception:
                logger.warning("repo_review github decode failed repo={}/{} path={}", owner, repo, path)
                return None
        return str(content) if content else None
