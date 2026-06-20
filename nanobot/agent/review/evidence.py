"""Evidence retrieval service for code-review workflows."""

from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path

from loguru import logger

from nanobot.agent.review.github import GitHubRepoReader
from nanobot.agent.review.utils import clean_scope_paths, parse_repo, path_matches_scope
from nanobot.rag.review import (
    DEFAULT_TEXT_EXTS,
    REMOTE_SOURCE_TYPE,
    SOURCE_TYPE,
    RepositoryRAGRequest,
    RepositoryRAGService,
)

_DEFAULT_TEXT_EXTS = DEFAULT_TEXT_EXTS


class ReviewEvidenceService:
    """Compose local/git/GitHub inputs with repository RAG retrieval."""

    def __init__(
        self,
        rag_service: RepositoryRAGService,
        github: GitHubRepoReader | None = None,
        *,
        workspace: Path | None = None,
    ) -> None:
        self.repository_rag = rag_service
        self.workspace = (workspace or rag_service.workspace).expanduser().resolve()
        self.github = github or GitHubRepoReader(workspace=self.workspace)

    async def local_context(
        self,
        *,
        review_query: str | None,
        max_results: int,
        include_tests: bool | None,
    ) -> str:
        trace_id = "local"
        started = time.perf_counter()
        if not review_query or not review_query.strip():
            logger.info(
                "review.evidence.local.done 🔎 trace_id={} status=error reason=missing_query elapsed_ms={:.1f}",
                trace_id,
                (time.perf_counter() - started) * 1000,
            )
            return "Error: review_query is required."

        result = await self.repository_rag.retrieve(
            RepositoryRAGRequest(
                source_type=SOURCE_TYPE,
                review_query=review_query.strip(),
                files=list(self.repository_rag.iter_candidate_files()),
                max_results=max_results,
                include_tests=include_tests,
                related_tests=False,
            )
        )
        if not result.hits:
            logger.info(
                "review.evidence.local.done 🔎 trace_id={} status=no_hits files_count=0 hits_count=0 context_chars={} elapsed_ms={:.1f}",
                trace_id,
                len(result.context),
                (time.perf_counter() - started) * 1000,
            )
            return "No relevant repository review references found."
        logger.info(
            "review.evidence.local.done ✅ trace_id={} status=success hits_count={} context_chars={} elapsed_ms={:.1f}",
            trace_id,
            len(result.hits),
            len(result.context),
            (time.perf_counter() - started) * 1000,
        )
        return result.context

    async def local_changed_context(
        self,
        *,
        review_query: str | None,
        target_paths: list[str],
        max_results: int,
        include_tests: bool | None,
    ) -> str:
        started = time.perf_counter()
        changed = await asyncio.to_thread(self.local_changed_files)
        scopes = clean_scope_paths(target_paths)
        if scopes:
            changed = [path for path in changed if path_matches_scope(path, scopes)]
        query = review_query or "code review local changed files regressions tests security"
        if changed:
            query = f"{query} {' '.join(changed[:40])}"
        block = await self.local_context(
            review_query=query,
            max_results=max_results,
            include_tests=include_tests,
        )
        if not changed:
            scope_line = f"- scope paths: {', '.join(scopes[:20])}\n" if scopes else ""
            result = "[Local Diff Review Context]\n" + scope_line + "- changed files: unavailable or none\n\n" + block
            logger.info(
                "review.evidence.local_changed.done 🔎 trace_id={} status=no_changed_files scopes_count={} changed_files=0 context_chars={} elapsed_ms={:.1f}",
                "local_changed",
                len(scopes),
                len(result),
                (time.perf_counter() - started) * 1000,
            )
            return result
        result = (
            "[Local Diff Review Context]\n"
            + (f"- scope paths: {', '.join(scopes[:20])}\n" if scopes else "")
            + f"- changed files: {len(changed)}\n"
            + "\n".join(f"  - {path}" for path in changed[:80])
            + "\n\n"
            + block
        )
        logger.info(
            "review.evidence.local_changed.done ✅ trace_id={} status=success scopes_count={} changed_files={} context_chars={} elapsed_ms={:.1f}",
            "local_changed",
            len(scopes),
            len(changed),
            len(result),
            (time.perf_counter() - started) * 1000,
        )
        return result

    def local_changed_files(self) -> list[str]:
        try:
            from git import Repo  # type: ignore
        except Exception as exc:
            logger.debug("repo_review GitPython unavailable reason={}", exc)
            return []
        try:
            repo = Repo(self.workspace, search_parent_directories=True)
            paths = set(repo.git.diff("--name-only").splitlines())
            paths.update(repo.git.diff("--name-only", "--cached").splitlines())
            paths.update(str(p) for p in repo.untracked_files)
            return sorted(path for path in paths if path and Path(path).suffix.lower() in _DEFAULT_TEXT_EXTS)
        except Exception as exc:
            logger.warning("repo_review local git diff unavailable reason={}", exc)
            return []

    async def local_targeted_context(
        self,
        *,
        review_query: str | None,
        target_paths: list[str],
        max_results: int,
        include_tests: bool | None,
    ) -> str:
        started = time.perf_counter()
        cleaned = clean_scope_paths(target_paths)
        if not cleaned:
            logger.info(
                "review.evidence.local_targeted.done 🔎 trace_id={} status=error reason=missing_target_paths elapsed_ms={:.1f}",
                "local_targeted",
                (time.perf_counter() - started) * 1000,
            )
            return "Error: target_paths is required for limited full_repo review."
        query = review_query or "code review targeted files security tests architecture"
        query = f"{query} {' '.join(cleaned[:40])}"
        block = await self.local_context(
            review_query=query,
            max_results=max_results,
            include_tests=include_tests,
        )
        header = "[Limited Full Repo Review Context]\n" + "\n".join(f"- {path}" for path in cleaned[:80])
        result = header + "\n\n" + block
        logger.info(
            "review.evidence.local_targeted.done ✅ trace_id={} status=success scopes_count={} context_chars={} elapsed_ms={:.1f}",
            "local_targeted",
            len(cleaned),
            len(result),
            (time.perf_counter() - started) * 1000,
        )
        return result

    async def retrieve_snapshot_context(
        self,
        *,
        snapshot_name: str,
        files: dict[str, str],
        review_query: str,
        max_results: int,
        include_tests: bool | None,
        trace_id: str,
        touched_lines: dict[str, list[int]] | None = None,
        related_tests: bool = True,
    ) -> tuple[Path | None, str, int]:
        started = time.perf_counter()
        result = await self.repository_rag.retrieve(
            RepositoryRAGRequest(
                source_type=REMOTE_SOURCE_TYPE,
                snapshot_name=snapshot_name,
                snapshot_files=files,
                review_query=review_query,
                max_results=max_results,
                include_tests=include_tests,
                touched_lines=touched_lines,
                related_tests=related_tests,
                trace_id=trace_id,
            )
        )
        logger.info(
            "review.evidence.snapshot.done ✅ trace_id={} snapshot={} files_count={} hits_count={} context_chars={} cache={} elapsed_ms={:.1f}",
            trace_id,
            snapshot_name,
            len(files),
            len(result.hits),
            len(result.context),
            result.cache_root,
            (time.perf_counter() - started) * 1000,
        )
        return result.cache_root, result.context, len(result.hits)

    async def github_context(
        self,
        *,
        repo: str,
        ref: str | None,
        tree_pattern: str | None,
        review_query: str | None,
        max_results: int,
        include_tests: bool | None,
        trace_id: str,
    ) -> str:
        started = time.perf_counter()
        if not review_query or not review_query.strip():
            review_query = "code review security architecture tests performance entry points config"
        try:
            snapshot, files = await self.github.fetch_text_files(
                repo,
                ref=ref,
                pattern=tree_pattern,
                max_files=self.github.config.max_index_files,
                trace_id=trace_id,
            )
        except Exception as exc:
            logger.exception("repo_review github context failed trace_id={}", trace_id)
            return f"Error: failed to fetch GitHub repository context: {exc}"
        if not files:
            logger.info(
                "review.evidence.github_context.done 🔎 trace_id={} status=empty_files repo={} files_count=0 elapsed_ms={:.1f}",
                trace_id,
                repo,
                (time.perf_counter() - started) * 1000,
            )
            return "No text files found for GitHub repository context retrieval."
        cache_root, context, hits_count = await self.retrieve_snapshot_context(
            snapshot_name=snapshot,
            files=files,
            review_query=review_query,
            max_results=max_results,
            include_tests=include_tests,
            trace_id=trace_id,
        )
        logger.info(
            "repo_review github context trace_id={} snapshot={} cache={} files={} hits={}",
            trace_id,
            snapshot,
            cache_root,
            len(files),
            hits_count,
        )
        if hits_count <= 0:
            logger.info(
                "review.evidence.github_context.done 🔎 trace_id={} status=no_hits repo={} snapshot={} files_count={} hits_count=0 context_chars={} elapsed_ms={:.1f}",
                trace_id,
                repo,
                snapshot,
                len(files),
                len(context),
                (time.perf_counter() - started) * 1000,
            )
            return "No relevant GitHub repository review references found."
        logger.info(
            "review.evidence.github_context.done ✅ trace_id={} status=success repo={} snapshot={} files_count={} hits_count={} context_chars={} elapsed_ms={:.1f}",
            trace_id,
            repo,
            snapshot,
            len(files),
            hits_count,
            len(context),
            (time.perf_counter() - started) * 1000,
        )
        return context

    async def github_targeted_context(
        self,
        *,
        repo: str,
        ref: str | None,
        target_paths: list[str],
        review_query: str | None,
        max_results: int,
        include_tests: bool | None,
        trace_id: str,
    ) -> str:
        started = time.perf_counter()
        cleaned = clean_scope_paths(target_paths, remote=True)
        if not cleaned:
            logger.info(
                "review.evidence.github_targeted.done 🔎 trace_id={} status=error reason=missing_target_paths repo={} elapsed_ms={:.1f}",
                trace_id,
                repo,
                (time.perf_counter() - started) * 1000,
            )
            return "Error: target_paths is required for limited full_repo review."
        files: dict[str, str] = {}
        snapshot_name = repo
        owner, repo_name = parse_repo(repo)
        for path in cleaned[:80]:
            text = await self.github._fetch_file_text(owner, repo_name, path, ref, trace_id=trace_id)
            if text is not None:
                files[path] = text
        if not files:
            logger.info(
                "review.evidence.github_targeted.done 🔎 trace_id={} status=empty_files repo={} scopes_count={} files_count=0 elapsed_ms={:.1f}",
                trace_id,
                repo,
                len(cleaned),
                (time.perf_counter() - started) * 1000,
            )
            return "No text files found for targeted GitHub review retrieval."
        query = review_query or "code review targeted files security tests architecture"
        query = f"{query} {' '.join(cleaned[:40])}"
        cache_root, context, hits_count = await self.retrieve_snapshot_context(
            snapshot_name=f"{snapshot_name}:targeted:{hashlib.sha256('|'.join(cleaned).encode('utf-8')).hexdigest()[:8]}",
            files=files,
            review_query=query,
            max_results=max_results,
            include_tests=include_tests,
            trace_id=trace_id,
        )
        logger.info(
            "repo_review.github_targeted trace_id={} repo={} cache={} files={} hits={}",
            trace_id,
            repo,
            cache_root,
            len(files),
            hits_count,
        )
        header = "[Limited GitHub Full Repo Review Context]\n" + "\n".join(f"- {path}" for path in cleaned[:80]) + "\n\n"
        if hits_count <= 0:
            result = header + "No relevant targeted GitHub repository review references found."
            logger.info(
                "review.evidence.github_targeted.done 🔎 trace_id={} status=no_hits repo={} scopes_count={} files_count={} hits_count=0 context_chars={} elapsed_ms={:.1f}",
                trace_id,
                repo,
                len(cleaned),
                len(files),
                len(result),
                (time.perf_counter() - started) * 1000,
            )
            return result
        result = header + context
        logger.info(
            "review.evidence.github_targeted.done ✅ trace_id={} status=success repo={} scopes_count={} files_count={} hits_count={} context_chars={} elapsed_ms={:.1f}",
            trace_id,
            repo,
            len(cleaned),
            len(files),
            hits_count,
            len(result),
            (time.perf_counter() - started) * 1000,
        )
        return result

    async def github_diff_context(
        self,
        *,
        repo: str,
        pr_number: int,
        target_paths: list[str],
        review_query: str | None,
        max_results: int,
        include_tests: bool | None,
        trace_id: str,
    ) -> str:
        started = time.perf_counter()
        if pr_number <= 0:
            logger.info(
                "review.evidence.github_diff.done 🔎 trace_id={} status=error reason=missing_pr_number repo={} elapsed_ms={:.1f}",
                trace_id,
                repo,
                (time.perf_counter() - started) * 1000,
            )
            return "Error: pr_number is required for action='pr_diff'."
        if not review_query or not review_query.strip():
            review_query = "code review changed lines regressions security tests"
        try:
            snapshot, files, touched_lines = await self.github.fetch_pr_files(
                repo,
                pr_number=pr_number,
                trace_id=trace_id,
            )
        except Exception as exc:
            logger.exception("repo_review github diff failed trace_id={}", trace_id)
            return f"Error: failed to fetch GitHub PR diff context: {exc}"
        scopes = clean_scope_paths(target_paths, remote=True)
        if scopes:
            files = {path: text for path, text in files.items() if path_matches_scope(path, scopes)}
            touched_lines = {
                path: lines for path, lines in touched_lines.items() if path_matches_scope(path, scopes)
            }
        if not files:
            logger.info(
                "review.evidence.github_diff.done 🔎 trace_id={} status=empty_files repo={} pr={} scopes_count={} files_count=0 elapsed_ms={:.1f}",
                trace_id,
                repo,
                pr_number,
                len(scopes),
                (time.perf_counter() - started) * 1000,
            )
            return "No text files found for GitHub PR diff retrieval."
        cache_root, context, hits_count = await self.retrieve_snapshot_context(
            snapshot_name=snapshot,
            files=files,
            review_query=review_query,
            max_results=max_results,
            include_tests=include_tests,
            trace_id=trace_id,
            touched_lines=touched_lines,
            related_tests=False,
        )
        header = [
            "[GitHub PR Diff Review Context]",
            f"- repository/pr: {snapshot}",
            *( [f"- scope paths: {', '.join(scopes[:20])}"] if scopes else [] ),
            f"- cached files: {len(files)}",
            f"- cache: {cache_root}",
            "",
        ]
        if hits_count <= 0:
            result = "\n".join(header) + "No relevant GitHub PR diff references found."
            logger.info(
                "review.evidence.github_diff.done 🔎 trace_id={} status=no_hits repo={} pr={} snapshot={} scopes_count={} files_count={} hits_count=0 context_chars={} elapsed_ms={:.1f}",
                trace_id,
                repo,
                pr_number,
                snapshot,
                len(scopes),
                len(files),
                len(result),
                (time.perf_counter() - started) * 1000,
            )
            return result
        result = "\n".join(header) + context
        logger.info(
            "review.evidence.github_diff.done ✅ trace_id={} status=success repo={} pr={} snapshot={} scopes_count={} files_count={} hits_count={} context_chars={} elapsed_ms={:.1f}",
            trace_id,
            repo,
            pr_number,
            snapshot,
            len(scopes),
            len(files),
            hits_count,
            len(result),
            (time.perf_counter() - started) * 1000,
        )
        return result
