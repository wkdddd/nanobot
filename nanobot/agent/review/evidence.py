"""Evidence retrieval service for code-review workflows."""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from nanobot.agent.review.github import GitHubRepoReader
from nanobot.agent.review.utils import (
    changed_lines_from_patch,
    clean_scope_paths,
    parse_repo,
    path_matches_scope,
)
from nanobot.rag.review_service import (
    DEFAULT_TEXT_EXTS,
    REMOTE_SOURCE_TYPE,
    SOURCE_TYPE,
    RepositoryRAGRequest,
    RepositoryRAGService,
)
from nanobot.utils.log_style import event_message, log_event


@dataclass(slots=True)
class LocalChangedSummary:
    files: list[str] = field(default_factory=list)
    touched_lines: dict[str, list[int]] = field(default_factory=dict)


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

    async def dispatch(
        self,
        *,
        target_type: str,
        action: str,
        repo: str = "",
        ref: str | None = None,
        pr_number: int = 0,
        target_paths: list[str] | None = None,
        tree_pattern: str | None = None,
        review_query: str | None = None,
        max_results: int = 5,
        include_tests: bool | None = None,
        trace_id: str = "",
    ) -> str:
        """Unified entry point that routes to the appropriate evidence method."""
        if target_type == "github":
            if action == "diff":
                return await self.github_diff_context(
                    repo=repo,
                    pr_number=pr_number,
                    target_paths=target_paths or [],
                    review_query=review_query,
                    max_results=max_results,
                    include_tests=include_tests,
                    trace_id=trace_id,
                )
            if target_paths:
                return await self.github_targeted_context(
                    repo=repo,
                    ref=ref,
                    target_paths=target_paths,
                    review_query=review_query,
                    max_results=max_results,
                    include_tests=include_tests,
                    trace_id=trace_id,
                )
            return await self.github_context(
                repo=repo,
                ref=ref,
                tree_pattern=tree_pattern,
                review_query=review_query,
                max_results=max_results,
                include_tests=include_tests,
                trace_id=trace_id,
            )
        if action == "diff":
            return await self.local_changed_context(
                review_query=review_query,
                target_paths=target_paths or [],
                max_results=max_results,
                include_tests=include_tests,
            )
        if target_paths:
            return await self.local_targeted_context(
                review_query=review_query,
                target_paths=target_paths,
                max_results=max_results,
                include_tests=include_tests,
            )
        return await self.local_context(
            review_query=review_query,
            max_results=max_results,
            include_tests=include_tests,
        )

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
            log_event(
                logger,
                "info",
                "review.evidence.local.done",
                status="error",
                trace_id=trace_id,
                reason="missing_query",
                elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
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
            log_event(
                logger,
                "info",
                "review.evidence.local.done",
                status="no_hits",
                trace_id=trace_id,
                files_count=0,
                hits_count=0,
                context_chars=len(result.context),
                elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
            )
            return "No relevant repository review references found."
        log_event(
            logger,
            "info",
            "review.evidence.local.done",
            status="success",
            trace_id=trace_id,
            hits_count=len(result.hits),
            context_chars=len(result.context),
            elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
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
        summary = await asyncio.to_thread(self.local_changed_summary)
        changed = summary.files
        touched_lines = summary.touched_lines
        scopes = clean_scope_paths(target_paths)
        if scopes:
            changed = [path for path in changed if path_matches_scope(path, scopes)]
            touched_lines = {
                path: lines for path, lines in touched_lines.items() if path_matches_scope(path, scopes)
            }
        query = review_query or "code review local changed files regressions tests security"
        if changed:
            query = f"{query} {' '.join(changed[:40])}"
        result = await self.repository_rag.retrieve(
            RepositoryRAGRequest(
                source_type=SOURCE_TYPE,
                review_query=query.strip(),
                files=list(self.repository_rag.iter_candidate_files()),
                max_results=max_results,
                include_tests=include_tests,
                touched_lines=touched_lines,
                related_tests=False,
                trace_id="local_changed",
            )
        )
        block = result.context if result.hits else "No relevant repository review references found."
        if not changed:
            scope_line = f"- scope paths: {', '.join(scopes[:20])}\n" if scopes else ""
            result = "[Local Diff Review Context]\n" + scope_line + "- changed files: unavailable or none\n\n" + block
            log_event(
                logger,
                "info",
                "review.evidence.local_changed.done",
                status="empty",
                trace_id="local_changed",
                reason="no_changed_files",
                scopes_count=len(scopes),
                changed_files=0,
                context_chars=len(result),
                elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
            )
            return result
        result = (
            "[Local Diff Review Context]\n"
            + (f"- scope paths: {', '.join(scopes[:20])}\n" if scopes else "")
            + f"- changed files: {len(changed)}\n"
            + f"- touched files: {len(touched_lines)}\n"
            + "\n".join(f"  - {path}" for path in changed[:80])
            + "\n\n"
            + block
        )
        log_event(
            logger,
            "info",
            "review.evidence.local_changed.done",
            status="success",
            trace_id="local_changed",
            scopes_count=len(scopes),
            changed_files=len(changed),
            context_chars=len(result),
            elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
        )
        return result

    def local_changed_summary(self) -> LocalChangedSummary:
        try:
            from git import Repo  # type: ignore
        except Exception as exc:
            logger.debug("repo_review GitPython unavailable reason={}", exc)
            return self._local_changed_summary_cli()
        try:
            repo = Repo(self.workspace, search_parent_directories=True)
            paths = set(repo.git.diff("--name-only").splitlines())
            paths.update(repo.git.diff("--name-only", "--cached").splitlines())
            paths.update(str(p) for p in repo.untracked_files)
            text_paths = sorted(
                path for path in paths if path and Path(path).suffix.lower() in DEFAULT_TEXT_EXTS
            )
            touched: dict[str, list[int]] = {}
            for path in text_paths:
                lines: set[int] = set()
                lines.update(
                    changed_lines_from_patch(path, self._local_diff_patch(repo, path, cached=False))
                )
                lines.update(
                    changed_lines_from_patch(path, self._local_diff_patch(repo, path, cached=True))
                )
                if path in repo.untracked_files:
                    lines.update(self._untracked_file_lines(repo, path))
                if lines:
                    touched[path] = sorted(lines)
            return LocalChangedSummary(files=text_paths, touched_lines=touched)
        except Exception as exc:
            logger.warning("repo_review local git diff unavailable reason={}", exc)
            return LocalChangedSummary()

    def local_changed_files(self) -> list[str]:
        return self.local_changed_summary().files

    def _local_changed_summary_cli(self) -> LocalChangedSummary:
        try:
            paths = set(self._git_cli("diff", "--name-only").splitlines())
            paths.update(self._git_cli("diff", "--name-only", "--cached").splitlines())
            paths.update(self._git_cli("ls-files", "--others", "--exclude-standard").splitlines())
            text_paths = sorted(
                path for path in paths if path and Path(path).suffix.lower() in DEFAULT_TEXT_EXTS
            )
            untracked = set(self._git_cli("ls-files", "--others", "--exclude-standard").splitlines())
            touched: dict[str, list[int]] = {}
            for path in text_paths:
                lines: set[int] = set()
                lines.update(
                    changed_lines_from_patch(path, self._local_diff_patch_cli(path, cached=False))
                )
                lines.update(
                    changed_lines_from_patch(path, self._local_diff_patch_cli(path, cached=True))
                )
                if path in untracked:
                    lines.update(self._untracked_workspace_file_lines(path))
                if lines:
                    touched[path] = sorted(lines)
            return LocalChangedSummary(files=text_paths, touched_lines=touched)
        except Exception as exc:
            logger.warning("repo_review local git cli diff unavailable reason={}", exc)
            return LocalChangedSummary()

    def _git_cli(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.workspace,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.stdout

    @staticmethod
    def _local_diff_patch(repo: object, path: str, *, cached: bool) -> str:
        args = ["--cached"] if cached else []
        args.extend(["--unified=0", "--", path])
        patch = repo.git.diff(*args)
        return ReviewEvidenceService._diff_hunk_lines(patch)

    def _local_diff_patch_cli(self, path: str, *, cached: bool) -> str:
        args = ["diff"]
        if cached:
            args.append("--cached")
        args.extend(["--unified=0", "--", path])
        return self._diff_hunk_lines(self._git_cli(*args))

    @staticmethod
    def _diff_hunk_lines(patch: str) -> str:
        return "\n".join(
            line
            for line in patch.splitlines()
            if line.startswith("@@")
            or (line.startswith("+") and not line.startswith("+++"))
            or (line.startswith("-") and not line.startswith("---"))
            or line.startswith(" ")
        )

    def _untracked_file_lines(self, repo: object, path: str) -> list[int]:
        worktree = getattr(repo, "working_tree_dir", None)
        if not worktree:
            return []
        target = (Path(worktree) / path).resolve()
        try:
            target.relative_to(Path(worktree).resolve())
        except ValueError:
            return []
        if target.suffix.lower() not in DEFAULT_TEXT_EXTS:
            return []
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        return list(range(1, len(text.replace("\r\n", "\n").replace("\r", "\n").splitlines()) + 1))

    def _untracked_workspace_file_lines(self, path: str) -> list[int]:
        target = (self.workspace / path).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError:
            return []
        if target.suffix.lower() not in DEFAULT_TEXT_EXTS:
            return []
        try:
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        return list(range(1, len(text.replace("\r\n", "\n").replace("\r", "\n").splitlines()) + 1))

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
            log_event(
                logger,
                "info",
                "review.evidence.local_targeted.done",
                status="error",
                trace_id="local_targeted",
                reason="missing_target_paths",
                elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
            )
            return "Error: target_paths is required for limited repo review."
        query = review_query or "code review targeted files security tests architecture"
        query = f"{query} {' '.join(cleaned[:40])}"
        block = await self.local_context(
            review_query=query,
            max_results=max_results,
            include_tests=include_tests,
        )
        header = "[Limited Full Repo Review Context]\n" + "\n".join(f"- {path}" for path in cleaned[:80])
        result = header + "\n\n" + block
        log_event(
            logger,
            "info",
            "review.evidence.local_targeted.done",
            status="success",
            trace_id="local_targeted",
            scopes_count=len(cleaned),
            context_chars=len(result),
            elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
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
        log_event(
            logger,
            "info",
            "review.evidence.snapshot.done",
            status="success",
            trace_id=trace_id,
            snapshot=snapshot_name,
            files_count=len(files),
            hits_count=len(result.hits),
            context_chars=len(result.context),
            cache=result.cache_root,
            elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
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
            logger.opt(exception=True, colors=True).error(
                event_message(
                    "review.evidence.github_context.failed",
                    status="failed",
                    trace_id=trace_id,
                )
            )
            return f"Error: failed to fetch GitHub repository context: {exc}"
        if not files:
            log_event(
                logger,
                "info",
                "review.evidence.github_context.done",
                status="empty_files",
                trace_id=trace_id,
                repo=repo,
                files_count=0,
                elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
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
        log_event(
            logger,
            "info",
            "review.evidence.github_context.cache",
            status="done",
            trace_id=trace_id,
            snapshot=snapshot,
            cache=cache_root,
            files=len(files),
            hits=hits_count,
        )
        if hits_count <= 0:
            log_event(
                logger,
                "info",
                "review.evidence.github_context.done",
                status="no_hits",
                trace_id=trace_id,
                repo=repo,
                snapshot=snapshot,
                files_count=len(files),
                hits_count=0,
                context_chars=len(context),
                elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
            )
            return "No relevant GitHub repository review references found."
        log_event(
            logger,
            "info",
            "review.evidence.github_context.done",
            status="success",
            trace_id=trace_id,
            repo=repo,
            snapshot=snapshot,
            files_count=len(files),
            hits_count=hits_count,
            context_chars=len(context),
            elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
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
            log_event(
                logger,
                "info",
                "review.evidence.github_targeted.done",
                status="error",
                trace_id=trace_id,
                reason="missing_target_paths",
                repo=repo,
                elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
            )
            return "Error: target_paths is required for limited repo review."
        files: dict[str, str] = {}
        snapshot_name = repo
        owner, repo_name = parse_repo(repo)
        for path in cleaned[:80]:
            text = await self.github._fetch_file_text(owner, repo_name, path, ref, trace_id=trace_id)
            if text is not None:
                files[path] = text
        if not files:
            log_event(
                logger,
                "info",
                "review.evidence.github_targeted.done",
                status="empty_files",
                trace_id=trace_id,
                repo=repo,
                scopes_count=len(cleaned),
                files_count=0,
                elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
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
        log_event(
            logger,
            "info",
            "review.evidence.github_targeted.cache",
            status="done",
            trace_id=trace_id,
            repo=repo,
            cache=cache_root,
            files=len(files),
            hits=hits_count,
        )
        header = "[Limited GitHub Full Repo Review Context]\n" + "\n".join(f"- {path}" for path in cleaned[:80]) + "\n\n"
        if hits_count <= 0:
            result = header + "No relevant targeted GitHub repository review references found."
            log_event(
                logger,
                "info",
                "review.evidence.github_targeted.done",
                status="no_hits",
                trace_id=trace_id,
                repo=repo,
                scopes_count=len(cleaned),
                files_count=len(files),
                hits_count=0,
                context_chars=len(result),
                elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
            )
            return result
        result = header + context
        log_event(
            logger,
            "info",
            "review.evidence.github_targeted.done",
            status="success",
            trace_id=trace_id,
            repo=repo,
            scopes_count=len(cleaned),
            files_count=len(files),
            hits_count=hits_count,
            context_chars=len(result),
            elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
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
            log_event(
                logger,
                "info",
                "review.evidence.github_diff.done",
                status="error",
                trace_id=trace_id,
                reason="missing_pr_number",
                repo=repo,
                elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
            )
            return "Error: pr_number is required for action='diff'."
        if not review_query or not review_query.strip():
            review_query = "code review changed lines regressions security tests"
        try:
            snapshot, files, touched_lines = await self.github.fetch_pr_files(
                repo,
                pr_number=pr_number,
                trace_id=trace_id,
            )
        except Exception as exc:
            logger.opt(exception=True, colors=True).error(
                event_message(
                    "review.evidence.github_diff.failed",
                    status="failed",
                    trace_id=trace_id,
                )
            )
            return f"Error: failed to fetch GitHub PR diff context: {exc}"
        scopes = clean_scope_paths(target_paths, remote=True)
        if scopes:
            files = {path: text for path, text in files.items() if path_matches_scope(path, scopes)}
            touched_lines = {
                path: lines for path, lines in touched_lines.items() if path_matches_scope(path, scopes)
            }
        if not files:
            log_event(
                logger,
                "info",
                "review.evidence.github_diff.done",
                status="empty_files",
                trace_id=trace_id,
                repo=repo,
                pr=pr_number,
                scopes_count=len(scopes),
                files_count=0,
                elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
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
            log_event(
                logger,
                "info",
                "review.evidence.github_diff.done",
                status="no_hits",
                trace_id=trace_id,
                repo=repo,
                pr=pr_number,
                snapshot=snapshot,
                scopes_count=len(scopes),
                files_count=len(files),
                hits_count=0,
                context_chars=len(result),
                elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
            )
            return result
        result = "\n".join(header) + context
        log_event(
            logger,
            "info",
            "review.evidence.github_diff.done",
            status="success",
            trace_id=trace_id,
            repo=repo,
            pr=pr_number,
            snapshot=snapshot,
            scopes_count=len(scopes),
            files_count=len(files),
            hits_count=hits_count,
            context_chars=len(result),
            elapsed_ms=f"{(time.perf_counter() - started) * 1000:.1f}",
        )
        return result
