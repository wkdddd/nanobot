"""Local and GitHub repository review tool."""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import hashlib
import json
import logging
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import httpx
from pydantic import AliasChoices, Field

from nanobot.rag import (
    IndexedChunk,
    QdrantVectorStore,
    RAGIndex,
    TreeSitterChunker,
    best_snippet,
    create_embedding_client_from_config,
    query_terms,
)
from nanobot.rag.utils import ChunkKey, IndexedHit
from nanobot.rag.rerank import create_rerank_client_from_config
from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import (
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.config.schema import Base

_GITHUB_URL_RE = re.compile(r"(?:https?://)?github\.com/([^/\s]+)/([^/\s.,;!?)#]+)")
_GITHUB_PR_URL_RE = re.compile(
    r"(?:https?://)?github\.com/([^/\s]+)/([^/\s.,;!?)#]+)/pull/(\d+)",
    re.I,
)
_SOURCE_TYPE = "code_review"
_REMOTE_SOURCE_TYPE = "code_review_github"
logger = logging.getLogger(__name__)

_DEFAULT_IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "htmlcov",
}

_DEFAULT_TEXT_EXTS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".yml",
    ".yaml",
    ".toml",
    ".css",
    ".html",
    ".txt",
}

_REVIEW_RISK_TERMS = {
    "security": {
        "auth",
        "token",
        "password",
        "secret",
        "jwt",
        "oauth",
        "permission",
        "csrf",
        "cors",
        "encrypt",
        "decrypt",
        "sql",
        "query",
        "exec",
        "shell",
        "subprocess",
        "path",
        "upload",
        "download",
        "ssrf",
    },
    "entrypoint": {
        "main",
        "app",
        "server",
        "router",
        "handler",
        "controller",
        "middleware",
        "api",
    },
    "config": {
        "config",
        "settings",
        "env",
        "dockerfile",
        "compose",
        "workflow",
        "ci",
        "pyproject",
        "package",
    },
    "test": {"test", "spec", "fixture", "mock"},
}


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


@dataclass(slots=True)
class RepoReviewHit:
    path: str
    score: float
    reason: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    snippet: str = ""
    start_line: int = 1
    end_line: int = 1
    kind: str = "text"


@dataclass(slots=True)
class RepoReviewChunk:
    path: str
    start_line: int
    end_line: int
    text: str
    symbols: list[str] = field(default_factory=list)
    kind: str = "text"


@dataclass(slots=True)
class RepoReviewRetrieverConfig:
    max_files: int = 2000
    max_file_chars: int = 80_000
    max_results: int = 8
    snippet_lines: int = 8
    chunk_lines: int = 80
    chunk_overlap: int = 12
    max_chunks_per_file: int = 40
    include_tests: bool = True
    enable_semantic: bool = False
    enable_chonkie: bool = True
    enable_rrf: bool = True
    report_dir: str = ".nanobot/review_reports"
    text_extensions: set[str] = field(default_factory=lambda: set(_DEFAULT_TEXT_EXTS))
    ignore_dirs: set[str] = field(default_factory=lambda: set(_DEFAULT_IGNORE_DIRS))
    ignore_globs: tuple[str, ...] = (
        "*.pyc",
        "*.pyo",
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.webp",
        "*.gif",
        "*.ico",
        "*.lock",
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _chunk_key(hit: IndexedHit) -> ChunkKey:
    chunk = hit.chunk
    return (chunk.path, int(chunk.start_line), int(chunk.end_line), chunk.kind)


def _rrf_merge(
    ranked_lists: list[tuple[str, list[IndexedHit]]],
    *,
    limit: int,
    k: int = 60,
) -> list[IndexedHit]:
    """Merge ranked retrieval lanes with reciprocal-rank fusion."""
    scores: dict[ChunkKey, float] = {}
    hits: dict[ChunkKey, IndexedHit] = {}
    reasons: dict[ChunkKey, list[str]] = {}
    for lane_name, lane_hits in ranked_lists:
        for rank, hit in enumerate(lane_hits, start=1):
            key = _chunk_key(hit)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            hits.setdefault(key, hit)
            merged_reasons = reasons.setdefault(key, [])
            if lane_name not in merged_reasons:
                merged_reasons.append(lane_name)
            for reason in hit.reason:
                if reason not in merged_reasons:
                    merged_reasons.append(reason)

    merged: list[IndexedHit] = []
    for key, score in scores.items():
        original = hits[key]
        merged.append(IndexedHit(chunk=original.chunk, score=score, reason=reasons.get(key, [])))
    merged.sort(key=lambda hit: -hit.score)
    return merged[:limit]


def _path_role_tags(rel_path: str, text: str = "") -> list[str]:
    path_low = rel_path.lower()
    text_low = text[:10_000].lower()
    tags: list[str] = []
    for tag, terms in _REVIEW_RISK_TERMS.items():
        if any(term in path_low or term in text_low for term in terms):
            tags.append(f"review:{tag}")
    suffix = Path(rel_path).suffix.lower()
    if suffix in {".json", ".toml", ".yaml", ".yml", ".ini", ".env"}:
        tags.append("review:config")
    if Path(rel_path).name.lower() in {"dockerfile", "makefile"}:
        tags.append("review:config")
    return list(dict.fromkeys(tags))


def _parse_pr_target(target: str | None) -> tuple[str | None, int | None]:
    if not target:
        return None, None
    match = _GITHUB_PR_URL_RE.search(target.strip())
    if not match:
        return None, None
    owner, repo, pr_number = match.group(1), match.group(2).removesuffix(".git"), int(match.group(3))
    return f"{owner}/{repo}", pr_number


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "repo"


def _parse_repo(repo: str) -> tuple[str, str]:
    repo = repo.strip().rstrip("/")
    m = _GITHUB_URL_RE.search(repo)
    if m:
        return m.group(1), m.group(2).removesuffix(".git")
    parts = repo.split("/")
    if len(parts) == 2 and all(parts):
        return parts[0], parts[1].removesuffix(".git")
    raise ValueError(f"Cannot parse GitHub repo: '{repo}'. Use 'owner/repo' or a GitHub URL.")


class LocalRepoReader:
    """Keyword and symbol based reader for local repository review."""

    def __init__(
        self,
        workspace: Path,
        config: RepoReviewRetrieverConfig | None = None,
        embedding_client: Any | None = None,
        rerank_client: Any | None = None,
        vector_store: Any | None = None,
        semantic_weight: float = 0.6,
        source_type: str = _SOURCE_TYPE,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.config = config or RepoReviewRetrieverConfig()
        self._semantic_weight = semantic_weight
        self.source_type = source_type
        self._chunker = TreeSitterChunker()
        self.index = RAGIndex(
            self.workspace,
            embedding_client=embedding_client,
            rerank_client=rerank_client,
            vector_store=vector_store,
            dimensions=getattr(embedding_client, "dimensions", 1024) if embedding_client else 1024,
        )

    async def async_retrieve(
        self, review_query: str, *, max_results: int | None = None
    ) -> list[RepoReviewHit]:
        start = time.perf_counter()
        terms = query_terms(review_query)
        if not terms:
            return []

        await asyncio.to_thread(self._sync_index)
        limit = max_results or self.config.max_results
        raw_hits = await self._retrieve_index_hits(review_query, limit=limit)
        logger.info(
            "repo_review local retrieval source_type=%s query_terms=%s hits=%s elapsed_ms=%.1f",
            self.source_type,
            len(terms),
            len(raw_hits),
            (time.perf_counter() - start) * 1000,
        )
        return [self._to_hit(h, terms) for h in raw_hits]

    def retrieve(self, review_query: str, *, max_results: int | None = None) -> list[RepoReviewHit]:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Sync fallback: FTS5-only (no embedding)
            terms = query_terms(review_query)
            if not terms:
                return []
            self._sync_index()
            fts_scores = self.index._fts5_search(
                self.source_type,
                review_query,
                limit=max_results or self.config.max_results,
            )
            chunks = self.index._load_chunks_by_keys(
                set(fts_scores.keys()), source_type=self.source_type
            )
            raw = []
            for key, score in sorted(fts_scores.items(), key=lambda x: -x[1]):
                chunk = chunks.get(key)
                if chunk:
                    raw.append(IndexedHit(chunk=chunk, score=score, reason=["bm25"]))
            return [self._to_hit(h, terms) for h in raw[: max_results or self.config.max_results]]
        else:
            return asyncio.run(self.async_retrieve(review_query, max_results=max_results))

    async def _retrieve_index_hits(self, review_query: str, *, limit: int) -> list[IndexedHit]:
        broad_hits = await self.index.search(
            source_type=self.source_type,
            query=review_query,
            max_hits=max(limit * 3, 20),
            semantic_weight=self._semantic_weight,
        )
        if not self.config.enable_rrf:
            return broad_hits[:limit]

        lanes: list[tuple[str, list[IndexedHit]]] = [("broad", broad_hits)]
        for lane_name, lane_query in self._review_lane_queries(review_query):
            lane_hits = self.index.lexical_search(
                self.source_type,
                lane_query,
                limit=max(limit * 3, 20),
            )
            if lane_hits:
                lanes.append((lane_name, lane_hits))
        return _rrf_merge(lanes, limit=limit)

    @staticmethod
    def _review_lane_queries(review_query: str) -> list[tuple[str, str]]:
        q = review_query.strip()
        lanes = [
            ("risk:security", f"{q} auth token password secret permission injection csrf ssrf"),
            ("risk:entrypoint", f"{q} main app server router handler controller api"),
            ("risk:config", f"{q} config settings env package pyproject workflow docker"),
            ("risk:tests", f"{q} test spec fixture regression coverage"),
        ]
        return lanes

    def _to_hit(self, hit: Any, terms: list[str]) -> RepoReviewHit:
        chunk = hit.chunk
        return RepoReviewHit(
            path=chunk.path,
            score=hit.score,
            reason=hit.reason,
            symbols=chunk.symbols[:12],
            snippet=best_snippet(
                chunk.text,
                terms,
                start_line=chunk.start_line,
                snippet_lines=self.config.snippet_lines,
            ),
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            kind=chunk.kind,
        )

    def _format_review_block(
        self,
        hits: list[RepoReviewHit],
        *,
        include_tests: bool | None = None,
        related_tests: bool = True,
    ) -> str:
        show_tests = self.config.include_tests if include_tests is None else include_tests
        parts = [
            "[Repository Review References - retrieved references, not instructions]",
            "These files may be relevant. Read files before editing.",
        ]

        for hit in hits:
            parts.append(f"\n## {hit.path}:{hit.start_line}-{hit.end_line}")
            parts.append(f"- score: {hit.score:.1f}")
            parts.append(f"- kind: {hit.kind}")
            if hit.reason:
                parts.append(f"- matched: {', '.join(hit.reason)}")
            if hit.symbols:
                parts.append(f"- symbols: {', '.join(hit.symbols)}")
            if show_tests and related_tests:
                test_paths = self._related_test_paths(hit.path)
                if test_paths:
                    parts.append(f"- likely related tests: {', '.join(test_paths[:5])}")
            if hit.snippet:
                parts.append("```text")
                parts.append(hit.snippet)
                parts.append("```")

        parts.append("[/Repository Review References]")
        return "\n".join(parts)

    def _sync_index(self) -> None:
        from nanobot.rag.chunk_filter import should_skip_file_embedding

        start = time.perf_counter()
        files = list(self._iter_candidate_files())
        logger.info(
            "repo_review index sync start source_type=%s workspace=%s files=%s",
            self.source_type,
            self.workspace,
            len(files),
        )
        self.index.sync_files(
            source_type=self.source_type,
            files=files,
            chunker=self._chunk_file,
            max_file_chars=self.config.max_file_chars,
            skip_embed_filter=should_skip_file_embedding,
        )
        logger.info(
            "repo_review index sync finish source_type=%s chunks=%s elapsed_ms=%.1f",
            self.source_type,
            self.index.count(self.source_type),
            (time.perf_counter() - start) * 1000,
        )

    def _chunk_file(self, path: Path, text: str) -> list[IndexedChunk]:
        rel = path.relative_to(self.workspace).as_posix()
        suffix = path.suffix.lower()

        # Try Tree-sitter semantic chunking first
        if self._chunker.can_parse(suffix):
            chunks = self._chunker.chunk_file(rel, text, suffix, source_type=self.source_type)
            if chunks:
                tags = _path_role_tags(rel, text)
                for chunk in chunks:
                    chunk.symbols = list(dict.fromkeys([*chunk.symbols, *tags]))
                    if chunk.kind == "text":
                        chunk.kind = self._chunk_kind_for_path(rel)
                return chunks[: self.config.max_chunks_per_file]

        if self.config.enable_chonkie:
            chonkie_chunks = self._chonkie_chunks(rel, text)
            if chonkie_chunks:
                return chonkie_chunks

        # Fallback: sliding window
        symbols = self._chunker.extract_symbols(text, suffix)
        symbols = list(dict.fromkeys([*symbols, *_path_role_tags(rel, text)]))
        return self._line_window_chunks(rel, text, symbols=symbols)

    def _chonkie_chunks(self, rel_path: str, text: str) -> list[IndexedChunk]:
        try:
            from chonkie import RecursiveChunker  # type: ignore
        except Exception as exc:
            logger.debug("repo_review chonkie unavailable path=%s reason=%s", rel_path, exc)
            return []

        try:
            chunker = RecursiveChunker(chunk_size=1800, min_characters_per_chunk=120)
            raw_chunks = chunker.chunk(text)
        except Exception as exc:
            logger.warning("repo_review chonkie chunk failed path=%s reason=%s", rel_path, exc)
            return []

        chunks: list[IndexedChunk] = []
        cursor = 0
        tags = _path_role_tags(rel_path, text)
        for raw in raw_chunks[: self.config.max_chunks_per_file]:
            chunk_text = getattr(raw, "text", str(raw)).strip()
            if not chunk_text:
                continue
            start_at = text.find(chunk_text[:80], cursor)
            if start_at < 0:
                start_at = cursor
            start_line = text.count("\n", 0, start_at) + 1
            end_line = start_line + chunk_text.count("\n")
            cursor = max(cursor, start_at + len(chunk_text))
            chunks.append(
                IndexedChunk(
                    source_type=self.source_type,
                    path=rel_path,
                    start_line=start_line,
                    end_line=max(start_line, end_line),
                    text=chunk_text,
                    symbols=tags[:12],
                    kind=self._chunk_kind_for_path(rel_path),
                )
            )
        if chunks:
            logger.debug("repo_review chonkie chunks path=%s chunks=%s", rel_path, len(chunks))
        return chunks

    def _iter_candidate_files(self) -> Iterable[Path]:
        count = 0
        for dirpath, dirnames, filenames in os.walk(self.workspace):
            current = Path(dirpath)
            try:
                rel_parts = current.relative_to(self.workspace).parts
            except ValueError:
                dirnames[:] = []
                continue
            if self._ignored_dir_parts(rel_parts):
                dirnames[:] = []
                continue
            dirnames[:] = [
                name
                for name in dirnames
                if not self._ignored_dir_parts((*rel_parts, name))
            ]
            for filename in filenames:
                if count >= self.config.max_files:
                    return
                path = current / filename
                if self._is_ignored(path):
                    continue
                if path.suffix.lower() not in self.config.text_extensions:
                    continue
                count += 1
                yield path

    def _is_ignored(self, path: Path) -> bool:
        try:
            rel_parts = path.relative_to(self.workspace).parts
        except ValueError:
            return True

        if self._ignored_dir_parts(rel_parts):
            return True

        return any(
            fnmatch.fnmatch(path.name, pattern)
            for pattern in self.config.ignore_globs
        )

    def _ignored_dir_parts(self, rel_parts: tuple[str, ...]) -> bool:
        if any(part in self.config.ignore_dirs for part in rel_parts):
            return True
        if rel_parts[:3] == ("references", "web", "pages"):
            return True
        return bool(rel_parts and rel_parts[0] == ".nanobot")

    def _line_window_chunks(
        self,
        rel_path: str,
        text: str,
        *,
        symbols: list[str],
    ) -> list[IndexedChunk]:
        lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        if not lines:
            return []

        chunk_size = max(1, self.config.chunk_lines)
        overlap = min(max(0, self.config.chunk_overlap), chunk_size - 1)
        step = max(1, chunk_size - overlap)

        chunks: list[IndexedChunk] = []
        start_index = 0
        while start_index < len(lines) and len(chunks) < self.config.max_chunks_per_file:
            end_index = min(len(lines), start_index + chunk_size)
            chunks.append(
                IndexedChunk(
                    source_type=self.source_type,
                    path=rel_path,
                    start_line=start_index + 1,
                    end_line=end_index,
                    text="\n".join(lines[start_index:end_index]),
                    symbols=symbols[:12],
                    kind=self._chunk_kind_for_path(rel_path),
                )
            )
            if end_index >= len(lines):
                break
            start_index += step

        return chunks

    @staticmethod
    def _chunk_kind_for_path(rel_path: str) -> str:
        suffix = Path(rel_path).suffix.lower()
        if suffix in {".md", ".txt"}:
            return "document"
        if suffix in {".json", ".toml", ".yaml", ".yml"}:
            return "config"
        return "text"

    def _related_test_paths(self, rel_path: str) -> list[str]:
        source = PureRepoPath(rel_path)
        candidates = source.test_candidates()
        found: list[str] = []
        for candidate in candidates:
            path = self.workspace / candidate
            if path.is_file():
                found.append(candidate)

        if found:
            return found

        stem_terms = [source.stem.lower()]
        if source.stem.startswith("test_"):
            stem_terms.append(source.stem[5:].lower())
        matches: list[str] = []
        tests_dir = self.workspace / "tests"
        if not tests_dir.is_dir():
            return []
        for path in tests_dir.rglob("*.py"):
            rel = path.relative_to(self.workspace).as_posix()
            name = path.stem.lower()
            if any(term and term in name for term in stem_terms):
                matches.append(rel)
        return sorted(dict.fromkeys(matches))[:5]


@dataclass(frozen=True, slots=True)
class PureRepoPath:
    rel_path: str

    @property
    def path(self) -> Path:
        return Path(self.rel_path)

    @property
    def stem(self) -> str:
        return self.path.stem

    def test_candidates(self) -> list[str]:
        path = self.path
        if not path.suffix:
            return []

        stem = path.stem
        suffix = path.suffix
        candidates = [
            Path("tests") / path.parent / f"test_{stem}{suffix}",
            Path("tests") / path.parent / f"{stem}_test{suffix}",
            Path("tests") / f"test_{stem}{suffix}",
            Path("tests") / f"{stem}_test{suffix}",
        ]
        if stem.startswith("test_"):
            base = stem[5:]
            candidates.extend(
                [
                    Path("tests") / f"test_{base}{suffix}",
                    Path("tests") / f"{base}_test{suffix}",
                ]
            )
        return list(dict.fromkeys(candidate.as_posix() for candidate in candidates))


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
            owner, repo_name = _parse_repo(repo)
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
            logger.info("repo_review github token loaded source=%s", source)
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

    async def _api_get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict | list | str:
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
                "repo_review github api timeout endpoint=%s timeout=%s",
                endpoint,
                self.config.timeout,
            )
            return f"Error: request to GitHub API timed out ({self.config.timeout}s)."
        except httpx.HTTPError as exc:
            logger.warning("repo_review github api http error endpoint=%s reason=%s", endpoint, exc)
            return f"Error: HTTP request failed: {exc}"

        if response.status_code == 403:
            remaining = response.headers.get("X-RateLimit-Remaining", "")
            if remaining == "0":
                reset = response.headers.get("X-RateLimit-Reset", "unknown")
                auth_hint = " Set GITHUB_TOKEN for higher limits (5000 req/hr)." if not token else ""
                logger.warning(
                    "repo_review github api rate limited endpoint=%s reset=%s authenticated=%s",
                    endpoint,
                    reset,
                    bool(token),
                )
                return f"Error: GitHub API rate limited. Resets at timestamp {reset}.{auth_hint}"
            logger.warning(
                "repo_review github api forbidden endpoint=%s authenticated=%s",
                endpoint,
                bool(token),
            )
            return "Error: access denied (403). The repo may be private; ensure GITHUB_TOKEN is set."
        if response.status_code == 404:
            logger.warning("repo_review github api not found endpoint=%s", endpoint)
            return "Error: repository or path not found. Check the URL and access permissions."
        if response.status_code >= 400:
            logger.warning(
                "repo_review github api error endpoint=%s status=%s body=%s",
                endpoint,
                response.status_code,
                response.text[:200],
            )
            return f"Error: GitHub API returned {response.status_code}: {response.text[:200]}"
        return response.json()

    async def _action_meta(self, owner: str, repo: str) -> str:
        data = await self._api_get(f"repos/{owner}/{repo}")
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
        return "\n".join(lines)

    async def _action_tree(
        self,
        owner: str,
        repo: str,
        ref: str | None,
        pattern: str | None,
        max_entries: int,
    ) -> str:
        if not ref:
            meta = await self._api_get(f"repos/{owner}/{repo}")
            if isinstance(meta, str):
                return meta
            ref = meta.get("default_branch", "main")

        data = await self._api_get(
            f"repos/{owner}/{repo}/git/trees/{ref}",
            params={"recursive": "1"},
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
        return header + "\n".join(entries) if entries else header + "(no matching entries)"

    async def _action_file(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str | None,
    ) -> str:
        params = {"ref": ref} if ref else {}
        data = await self._api_get(
            f"repos/{owner}/{repo}/contents/{path.lstrip('/')}",
            params,
        )
        if isinstance(data, str):
            return data

        if isinstance(data, list):
            lines = [f"Directory: {path}/"]
            for item in data[:200]:
                suffix = "/" if item.get("type") == "dir" else ""
                lines.append(f"  {item.get('name', '')}{suffix}")
            return "\n".join(lines)

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
        return header + decoded + truncated_note

    async def _action_diff(self, owner: str, repo: str, pr_number: int) -> str:
        data = await self._api_get(f"repos/{owner}/{repo}/pulls/{pr_number}/files")
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
        return "\n".join(lines)

    async def fetch_text_files(
        self,
        repo: str,
        *,
        ref: str | None = None,
        pattern: str | None = None,
        max_files: int | None = None,
    ) -> tuple[str, dict[str, str]]:
        pygithub_result = await asyncio.to_thread(
            self._fetch_text_files_pygithub,
            repo,
            ref,
            pattern,
            max_files,
        )
        if pygithub_result is not None:
            return pygithub_result

        owner, repo_name = _parse_repo(repo)
        if not ref:
            meta = await self._api_get(f"repos/{owner}/{repo_name}")
            if isinstance(meta, str):
                raise RuntimeError(meta)
            ref = meta.get("default_branch", "main")
        tree_data = await self._api_get(
            f"repos/{owner}/{repo_name}/git/trees/{ref}",
            params={"recursive": "1"},
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
            content = await self._fetch_file_text(owner, repo_name, path, ref)
            if content is not None:
                files[path] = content
            if len(files) >= limit:
                break
        logger.info(
            "repo_review github fetched files repo=%s/%s ref=%s files=%s limit=%s",
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
    ) -> tuple[str, dict[str, str], dict[str, list[int]]]:
        pygithub_result = await asyncio.to_thread(
            self._fetch_pr_files_pygithub,
            repo,
            pr_number,
        )
        if pygithub_result is not None:
            return pygithub_result

        owner, repo_name = _parse_repo(repo)
        pr_data = await self._api_get(f"repos/{owner}/{repo_name}/pulls/{pr_number}")
        head_ref = None
        if isinstance(pr_data, dict):
            head = pr_data.get("head")
            if isinstance(head, dict):
                head_ref = head.get("sha")
        data = await self._api_get(f"repos/{owner}/{repo_name}/pulls/{pr_number}/files")
        if isinstance(data, str):
            raise RuntimeError(data)
        files: dict[str, str] = {}
        touched: dict[str, list[int]] = {}
        for item in data[: self.config.max_patch_files]:
            filename = str(item.get("filename", ""))
            if not filename or Path(filename).suffix.lower() not in _DEFAULT_TEXT_EXTS:
                continue
            patch = item.get("patch") or ""
            touched[filename] = _changed_lines_from_patch(filename, patch)
            content = await self._fetch_file_text(owner, repo_name, filename, head_ref)
            if content is None and patch:
                content = patch
            if content is not None:
                files[filename] = content
        return f"{owner}/{repo_name}#{pr_number}", files, touched

    def _github_client(self) -> Any | None:
        try:
            from github import Github  # type: ignore
        except Exception as exc:
            logger.debug("repo_review PyGithub unavailable reason=%s", exc)
            return None
        token = self._workspace_config_token() or self.config.token.strip() or os.environ.get("GITHUB_TOKEN", "").strip()
        try:
            return Github(token or None, timeout=self.config.timeout)
        except Exception as exc:
            logger.warning("repo_review PyGithub client init failed reason=%s", exc)
            return None

    def _fetch_text_files_pygithub(
        self,
        repo: str,
        ref: str | None,
        pattern: str | None,
        max_files: int | None,
    ) -> tuple[str, dict[str, str]] | None:
        client = self._github_client()
        if client is None:
            return None
        try:
            owner, repo_name = _parse_repo(repo)
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
                "repo_review PyGithub fetched files repo=%s ref=%s files=%s",
                repo,
                ref_name,
                len(files),
            )
            return f"{repo_slug}@{ref_name}", files
        except Exception as exc:
            logger.warning("repo_review PyGithub full repo fallback repo=%s reason=%s", repo, exc)
            return None

    def _fetch_pr_files_pygithub(
        self,
        repo: str,
        pr_number: int,
    ) -> tuple[str, dict[str, str], dict[str, list[int]]] | None:
        client = self._github_client()
        if client is None:
            return None
        try:
            owner, repo_name = _parse_repo(repo)
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
                touched[filename] = _changed_lines_from_patch(filename, patch)
                try:
                    content_file = gh_repo.get_contents(filename, ref=pr.head.sha)
                    if isinstance(content_file, list):
                        continue
                    files[filename] = content_file.decoded_content.decode("utf-8", errors="replace")
                except Exception:
                    if patch:
                        files[filename] = patch
            logger.info(
                "repo_review PyGithub fetched pr files repo=%s pr=%s files=%s",
                repo,
                pr_number,
                len(files),
            )
            return f"{repo_slug}#{pr_number}", files, touched
        except Exception as exc:
            logger.warning("repo_review PyGithub PR fallback repo=%s pr=%s reason=%s", repo, pr_number, exc)
            return None

    async def _fetch_file_text(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str | None,
    ) -> str | None:
        params = {"ref": ref} if ref else {}
        data = await self._api_get(f"repos/{owner}/{repo}/contents/{path}", params)
        if isinstance(data, str) or isinstance(data, list):
            return None
        if int(data.get("size") or 0) > self.config.max_file_size:
            return None
        content = data.get("content", "")
        if data.get("encoding") == "base64" and content:
            try:
                return base64.b64decode(content).decode("utf-8", errors="replace")
            except Exception:
                logger.warning("repo_review github decode failed repo=%s/%s path=%s", owner, repo, path)
                return None
        return str(content) if content else None


def _changed_lines_from_patch(filename: str, patch: str) -> list[int]:
    if not patch:
        return []
    try:
        from unidiff import PatchSet  # type: ignore

        parsed = PatchSet(f"diff --git a/{filename} b/{filename}\n--- a/{filename}\n+++ b/{filename}\n{patch}")
        lines: list[int] = []
        for patched_file in parsed:
            for hunk in patched_file:
                for line in hunk:
                    if line.is_added:
                        if line.target_line_no is not None:
                            lines.append(int(line.target_line_no))
        return sorted(set(lines))
    except Exception as exc:
        logger.debug("repo_review unidiff fallback filename=%s reason=%s", filename, exc)
    lines = []
    current = 0
    for line in patch.splitlines():
        if line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            current = int(match.group(1)) if match else current
            continue
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(current)
            current += 1
        elif not line.startswith("-"):
            current += 1
    return sorted(set(line for line in lines if line > 0))


class CachedRepoReader:
    """Cache remote repository text under the workspace and query it with LocalRepoReader."""

    def __init__(
        self,
        *,
        workspace: Path,
        config: RepoReviewRetrieverConfig,
        embedding_client: Any | None,
        rerank_client: Any | None,
        vector_store: Any | None,
        semantic_weight: float,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.config = config
        self.embedding_client = embedding_client
        self.rerank_client = rerank_client
        self.vector_store = vector_store
        self.semantic_weight = semantic_weight

    def write_snapshot(self, snapshot_name: str, files: dict[str, str]) -> Path:
        digest = hashlib.sha256(snapshot_name.encode("utf-8")).hexdigest()[:12]
        cache_root = self.workspace / ".nanobot" / "review_github" / f"{_safe_slug(snapshot_name)}_{digest}"
        start = time.perf_counter()
        cache_root.mkdir(parents=True, exist_ok=True)
        manifest = {
            "snapshot": snapshot_name,
            "created_at": _now_iso(),
            "files": sorted(files),
        }
        for rel, text in files.items():
            target = (cache_root / rel).resolve()
            try:
                target.relative_to(cache_root)
            except ValueError:
                logger.warning("repo_review skipped unsafe remote path=%s", rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
        (cache_root / ".nanobot_snapshot.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        logger.info(
            "repo_review remote snapshot written snapshot=%s cache=%s files=%s elapsed_ms=%.1f",
            snapshot_name,
            cache_root,
            len(files),
            (time.perf_counter() - start) * 1000,
        )
        return cache_root

    async def retrieve(
        self,
        *,
        snapshot_name: str,
        files: dict[str, str],
        review_query: str,
        max_results: int,
        touched_lines: dict[str, list[int]] | None = None,
    ) -> tuple[Path, list[RepoReviewHit]]:
        cache_root = self.write_snapshot(snapshot_name, files)
        reader = LocalRepoReader(
            cache_root,
            config=self.config,
            embedding_client=self.embedding_client,
            rerank_client=self.rerank_client,
            vector_store=self.vector_store,
            semantic_weight=self.semantic_weight,
            source_type=_REMOTE_SOURCE_TYPE,
        )
        hits = await reader.async_retrieve(review_query, max_results=max_results)
        if touched_lines:
            touched_paths = set(touched_lines)
            for hit in hits:
                if hit.path in touched_paths:
                    hit.reason = list(dict.fromkeys(["diff-touched", *hit.reason]))
                    hit.score += 1.0
            hits.sort(key=lambda hit: -hit.score)
        return cache_root, hits[:max_results]


@tool_parameters(
    tool_parameters_schema(
        target_type=StringSchema(
            "Review target type: 'local' for current workspace search or 'github' for GitHub API reads",
            enum=("local", "github"),
        ),
        action=StringSchema(
            "Code review RAG action: context, diff, report, evaluate. Legacy GitHub actions: meta, tree, file",
            enum=("context", "diff", "report", "evaluate", "meta", "tree", "file"),
            nullable=True,
        ),
        target=StringSchema(
            "Optional local path, GitHub repo URL, or GitHub PR URL. PR URLs imply action='diff'.",
            nullable=True,
        ),
        target_repo=StringSchema(
            "GitHub repo in 'owner/repo' format or a full GitHub URL when target_type='github'",
            nullable=True,
        ),
        pr_number=IntegerSchema(
            0,
            description="GitHub pull request number for action='diff'",
            minimum=0,
            maximum=1000000,
        ),
        repo_path=StringSchema(
            "File path within the GitHub repo. Required for target_type='github' action='file'",
            nullable=True,
        ),
        ref=StringSchema("GitHub branch, tag, or commit SHA", nullable=True),
        tree_pattern=StringSchema(
            "Glob filter for GitHub tree results (e.g. '*.py')",
            nullable=True,
        ),
        review_query=StringSchema(
            "Question or keywords describing the repository review references to retrieve",
            nullable=True,
        ),
        max_results=IntegerSchema(
            5,
            description="Maximum number of local repository review references to return",
            minimum=1,
            maximum=20,
        ),
        include_tests=BooleanSchema(
            description="Include likely related test file paths when available",
            default=True,
        ),
        tree_limit=IntegerSchema(
            500,
            description="Maximum GitHub tree entries to return",
            minimum=1,
            maximum=10000,
        ),
        report_path=StringSchema(
            "Optional Markdown report output path for action='report' or action='evaluate'",
            nullable=True,
        ),
        dataset_path=StringSchema(
            "JSONL evaluation dataset path for action='evaluate'",
            nullable=True,
        ),
        budget_chars=IntegerSchema(
            16000,
            description="Maximum characters to include in generated Markdown reports",
            minimum=1000,
            maximum=100000,
        ),
        required=[],
    )
)
class RepoReviewTool(Tool):
    """Tool wrapper for repository review references."""

    _scopes = {"core", "subagent"}

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        embedding_config = getattr(ctx, "embedding_config", None)
        if embedding_config is None:
            embedding_config = getattr(ctx.config, "embedding", None)
        rerank_config = getattr(ctx, "rerank_config", None)
        if rerank_config is None:
            rerank_config = getattr(ctx.config, "rerank", None)
        qdrant_config = getattr(ctx, "qdrant_config", None)
        if qdrant_config is None:
            qdrant_config = getattr(ctx.config, "qdrant", None)
        embedding_client = create_embedding_client_from_config(embedding_config)
        rerank_client = create_rerank_client_from_config(rerank_config)
        vector_store = QdrantVectorStore.from_config(
            qdrant_config,
            dimensions=getattr(embedding_client, "dimensions", 1024) if embedding_client else 1024,
        )
        tools_config = getattr(ctx.config, "tools", None)
        return cls(
            workspace=Path(ctx.workspace),
            embedding_client=embedding_client,
            rerank_client=rerank_client,
            vector_store=vector_store,
            github_config=getattr(tools_config, "github_repo", None),
            semantic_weight=getattr(embedding_config, "semantic_weight", 0.6),
        )

    def __init__(
        self,
        workspace: Path,
        embedding_client: Any | None = None,
        rerank_client: Any | None = None,
        vector_store: Any | None = None,
        github_config: GitHubRepoConfig | None = None,
        semantic_weight: float = 0.6,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.embedding_client = embedding_client
        self.rerank_client = rerank_client
        self.vector_store = vector_store
        self.semantic_weight = semantic_weight
        config = RepoReviewRetrieverConfig(
            enable_semantic=embedding_client is not None,
        )
        self.config = config
        self.retriever = LocalRepoReader(
            workspace,
            config=config,
            embedding_client=embedding_client,
            rerank_client=rerank_client,
            vector_store=vector_store,
            semantic_weight=semantic_weight,
        )
        self.remote_retriever = CachedRepoReader(
            workspace=workspace,
            config=config,
            embedding_client=embedding_client,
            rerank_client=rerank_client,
            vector_store=vector_store,
            semantic_weight=semantic_weight,
        )
        self.github = GitHubRepoReader(github_config, workspace=workspace)

    @property
    def name(self) -> str:
        return "repo_review"

    @property
    def description(self) -> str:
        return (
            "CodeReview RAG tool for repository evidence retrieval, GitHub full-repo "
            "and PR diff context, Markdown context reports, and retrieval evaluation. "
            "Use action='context' for full-repo retrieval and action='diff' for PR/diff review."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(
        self,
        review_query: str | None = None,
        target_type: str = "local",
        action: str | None = None,
        target: str | None = None,
        target_repo: str | None = None,
        pr_number: int = 0,
        repo_path: str | None = None,
        ref: str | None = None,
        tree_pattern: str | None = None,
        max_results: int = 5,
        include_tests: bool | None = None,
        tree_limit: int = 500,
        report_path: str | None = None,
        dataset_path: str | None = None,
        budget_chars: int = 16000,
    ) -> str:
        trace_id = uuid.uuid4().hex[:8]
        started = time.perf_counter()
        action = (action or "context").strip().lower()
        logger.info(
            "repo_review start trace_id=%s action=%s target_type=%s target=%s target_repo=%s",
            trace_id,
            action,
            target_type,
            target,
            target_repo,
        )
        target_type = (target_type or "local").strip().lower()
        pr_repo, parsed_pr_number = _parse_pr_target(target)
        if pr_repo:
            target_type = "github"
            target_repo = target_repo or pr_repo
            pr_number = pr_number or parsed_pr_number or 0
            if action == "context":
                action = "diff"

        try:
            if action == "evaluate":
                return await self._evaluate(
                    dataset_path=dataset_path,
                    report_path=report_path,
                    max_results=max_results,
                    trace_id=trace_id,
                )
            if action == "report":
                return await self._report(
                    review_query=review_query,
                    target_type=target_type,
                    target=target,
                    target_repo=target_repo,
                    ref=ref,
                    max_results=max_results,
                    include_tests=include_tests,
                    report_path=report_path,
                    budget_chars=budget_chars,
                    trace_id=trace_id,
                )
            if target_type == "github":
                if not self.github.config.enable:
                    return "Error: GitHub repository access is disabled by tools.githubRepo.enable."
                repo = (target_repo or target or "").strip()
                if not repo:
                    return "Error: target_repo is required when target_type='github'."
                if action in {"meta", "tree", "file"}:
                    kwargs: dict[str, Any] = {
                        "action": action,
                        "repo": repo,
                        "path": repo_path,
                        "ref": ref,
                        "pattern": tree_pattern,
                        "max_entries": tree_limit,
                    }
                    return await self.github.execute(**kwargs)
                if action == "diff":
                    return await self._github_diff_context(
                        repo=repo,
                        pr_number=int(pr_number or 0),
                        review_query=review_query,
                        max_results=max_results,
                        include_tests=include_tests,
                        trace_id=trace_id,
                    )
                if action == "context":
                    return await self._github_context(
                        repo=repo,
                        ref=ref,
                        tree_pattern=tree_pattern,
                        review_query=review_query,
                        max_results=max_results,
                        include_tests=include_tests,
                        trace_id=trace_id,
                    )
                return f"Error: unknown GitHub action '{action}'."

            if action not in {"context", "diff"}:
                return f"Error: action '{action}' is not supported for local target_type."
            if action == "diff":
                return await self._local_diff_context(
                    review_query=review_query,
                    max_results=max_results,
                    include_tests=include_tests,
                )
            return await self._local_context(
                review_query=review_query,
                max_results=max_results,
                include_tests=include_tests,
            )
        finally:
            logger.info(
                "repo_review finish trace_id=%s action=%s elapsed_ms=%.1f",
                trace_id,
                action,
                (time.perf_counter() - started) * 1000,
            )

    async def _local_context(
        self,
        *,
        review_query: str | None,
        max_results: int,
        include_tests: bool | None,
    ) -> str:
        if not review_query or not review_query.strip():
            return "Error: review_query is required."

        hits = await self.retriever.async_retrieve(
            review_query.strip(),
            max_results=max_results,
        )
        if not hits:
            return "No relevant repository review references found."
        return self.retriever._format_review_block(
            hits,
            include_tests=include_tests,
            related_tests=False,
        )

    async def _local_diff_context(
        self,
        *,
        review_query: str | None,
        max_results: int,
        include_tests: bool | None,
    ) -> str:
        changed = await asyncio.to_thread(self._local_changed_files)
        query = review_query or "code review changed files regressions tests security"
        if changed:
            query = f"{query} {' '.join(changed[:40])}"
        block = await self._local_context(
            review_query=query,
            max_results=max_results,
            include_tests=include_tests,
        )
        if not changed:
            return "[Local Diff Review Context]\n- changed files: unavailable or none\n\n" + block
        return (
            "[Local Diff Review Context]\n"
            f"- changed files: {len(changed)}\n"
            + "\n".join(f"  - {path}" for path in changed[:80])
            + "\n\n"
            + block
        )

    def _local_changed_files(self) -> list[str]:
        try:
            from git import Repo  # type: ignore
        except Exception as exc:
            logger.debug("repo_review GitPython unavailable reason=%s", exc)
            return []
        try:
            repo = Repo(self.workspace, search_parent_directories=True)
            paths = set(repo.git.diff("--name-only").splitlines())
            paths.update(repo.git.diff("--name-only", "--cached").splitlines())
            paths.update(str(p) for p in repo.untracked_files)
            return sorted(path for path in paths if path and Path(path).suffix.lower() in _DEFAULT_TEXT_EXTS)
        except Exception as exc:
            logger.warning("repo_review local git diff unavailable reason=%s", exc)
            return []

    async def _github_context(
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
        if not review_query or not review_query.strip():
            review_query = "code review security architecture tests performance entry points config"
        try:
            snapshot, files = await self.github.fetch_text_files(
                repo,
                ref=ref,
                pattern=tree_pattern,
                max_files=self.github.config.max_index_files,
            )
        except Exception as exc:
            logger.exception("repo_review github context failed trace_id=%s", trace_id)
            return f"Error: failed to fetch GitHub repository context: {exc}"
        if not files:
            return "No text files found for GitHub repository context retrieval."
        cache_root, hits = await self.remote_retriever.retrieve(
            snapshot_name=snapshot,
            files=files,
            review_query=review_query,
            max_results=max_results,
        )
        logger.info(
            "repo_review github context trace_id=%s snapshot=%s cache=%s files=%s hits=%s",
            trace_id,
            snapshot,
            cache_root,
            len(files),
            len(hits),
        )
        if not hits:
            return "No relevant GitHub repository review references found."
        return self.retriever._format_review_block(hits, include_tests=include_tests)

    async def _github_diff_context(
        self,
        *,
        repo: str,
        pr_number: int,
        review_query: str | None,
        max_results: int,
        include_tests: bool | None,
        trace_id: str,
    ) -> str:
        if pr_number <= 0:
            return "Error: pr_number is required for action='diff'."
        if not review_query or not review_query.strip():
            review_query = "code review changed lines regressions security tests"
        try:
            snapshot, files, touched_lines = await self.github.fetch_pr_files(
                repo,
                pr_number=pr_number,
            )
        except Exception as exc:
            logger.exception("repo_review github diff failed trace_id=%s", trace_id)
            return f"Error: failed to fetch GitHub PR diff context: {exc}"
        if not files:
            return "No text files found for GitHub PR diff retrieval."
        cache_root, hits = await self.remote_retriever.retrieve(
            snapshot_name=snapshot,
            files=files,
            review_query=review_query,
            max_results=max_results,
            touched_lines=touched_lines,
        )
        header = [
            "[GitHub PR Diff Review Context]",
            f"- repository/pr: {snapshot}",
            f"- cached files: {len(files)}",
            f"- cache: {cache_root}",
            "",
        ]
        if not hits:
            return "\n".join(header) + "No relevant GitHub PR diff references found."
        return "\n".join(header) + self.retriever._format_review_block(
            hits,
            include_tests=include_tests,
            related_tests=False,
        )

    async def _report(
        self,
        *,
        review_query: str | None,
        target_type: str,
        target: str | None,
        target_repo: str | None,
        ref: str | None,
        max_results: int,
        include_tests: bool | None,
        report_path: str | None,
        budget_chars: int,
        trace_id: str,
    ) -> str:
        if target_type == "github":
            context = await self._github_context(
                repo=(target_repo or target or "").strip(),
                ref=ref,
                tree_pattern=None,
                review_query=review_query,
                max_results=max_results,
                include_tests=include_tests,
                trace_id=trace_id,
            )
        else:
            context = await self._local_context(
                review_query=review_query or "code review entry points security tests config",
                max_results=max_results,
                include_tests=include_tests,
            )
        markdown = "\n".join(
            [
                "# Code Review RAG Context Report",
                "",
                f"- Generated: {_now_iso()}",
                f"- Target type: {target_type}",
                f"- Target: {target_repo or target or 'local workspace'}",
                f"- Query: {review_query or '(default code review query)'}",
                "",
                "## Retrieved Evidence",
                "",
                context[: max(1000, int(budget_chars))],
                "",
            ]
        )
        path = self._resolve_report_path(report_path, "code-review-rag-report.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8", newline="\n")
        logger.info("repo_review report written trace_id=%s path=%s chars=%s", trace_id, path, len(markdown))
        return f"Markdown report written: {path}\n\n{markdown[:4000]}"

    async def _evaluate(
        self,
        *,
        dataset_path: str | None,
        report_path: str | None,
        max_results: int,
        trace_id: str,
    ) -> str:
        if not dataset_path:
            return "Error: dataset_path is required for action='evaluate'."
        path = Path(dataset_path).expanduser()
        if not path.is_absolute():
            path = self.workspace / path
        try:
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError) as exc:
            return f"Error: failed to read evaluation dataset: {exc}"

        results: list[dict[str, Any]] = []
        for i, row in enumerate(rows, start=1):
            query = str(row.get("question") or row.get("review_query") or "")
            if not query:
                continue
            target_type = str(row.get("target_type") or "local")
            target_repo = row.get("target_repo") or row.get("target")
            if target_type == "github" and target_repo:
                context = await self._github_context(
                    repo=str(target_repo),
                    ref=row.get("ref"),
                    tree_pattern=row.get("tree_pattern"),
                    review_query=query,
                    max_results=max_results,
                    include_tests=True,
                    trace_id=trace_id,
                )
            else:
                context = await self._local_context(
                    review_query=query,
                    max_results=max_results,
                    include_tests=True,
                )
            expected_files = [str(x) for x in row.get("expected_files", [])]
            expected_symbols = [str(x) for x in row.get("expected_symbols", [])]
            file_hits = sum(1 for f in expected_files if f and f in context)
            symbol_hits = sum(1 for s in expected_symbols if s and s in context)
            denom = max(1, len(expected_files) + len(expected_symbols))
            coverage = (file_hits + symbol_hits) / denom
            results.append(
                {
                    "id": row.get("id", i),
                    "query": query,
                    "expected_files": len(expected_files),
                    "expected_symbols": len(expected_symbols),
                    "file_hits": file_hits,
                    "symbol_hits": symbol_hits,
                    "evidence_coverage": round(coverage, 4),
                    "context_chars": len(context),
                }
            )

        average = (
            sum(float(r["evidence_coverage"]) for r in results) / len(results)
            if results
            else 0.0
        )
        lines = [
            "# Code Review RAG Evaluation Report",
            "",
            f"- Generated: {_now_iso()}",
            f"- Dataset: {path}",
            f"- Samples: {len(results)}",
            f"- Average evidence coverage: {average:.3f}",
            "",
            "| ID | Coverage | File hits | Symbol hits | Query |",
            "|---|---:|---:|---:|---|",
        ]
        for result in results:
            query = str(result["query"]).replace("|", "\\|")
            lines.append(
                f"| {result['id']} | {result['evidence_coverage']:.3f} | "
                f"{result['file_hits']}/{result['expected_files']} | "
                f"{result['symbol_hits']}/{result['expected_symbols']} | {query} |"
            )
        markdown = "\n".join(lines) + "\n"
        out_path = self._resolve_report_path(report_path, "code-review-rag-eval.md")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8", newline="\n")
        logger.info(
            "repo_review evaluation written trace_id=%s path=%s samples=%s average=%.3f",
            trace_id,
            out_path,
            len(results),
            average,
        )
        return f"Evaluation report written: {out_path}\n\n{markdown}"

    def _resolve_report_path(self, raw: str | None, default_name: str) -> Path:
        if raw and raw.strip():
            path = Path(raw.strip()).expanduser()
            if not path.is_absolute():
                path = self.workspace / path
            return path
        return self.workspace / self.config.report_dir / default_name
