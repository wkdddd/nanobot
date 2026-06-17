"""Local and GitHub repository review tool."""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import httpx
from pydantic import AliasChoices, Field

from nanobot.rag import (
    IndexedChunk,
    RAGIndex,
    TreeSitterChunker,
    best_snippet,
    create_embedding_client_from_config,
    query_terms,
)
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
        semantic_weight: float = 0.6,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.config = config or RepoReviewRetrieverConfig()
        self._semantic_weight = semantic_weight
        self._chunker = TreeSitterChunker()
        self.index = RAGIndex(
            self.workspace,
            embedding_client=embedding_client,
            rerank_client=rerank_client,
            dimensions=getattr(embedding_client, "dimensions", 1024) if embedding_client else 1024,
        )

    async def async_retrieve(
        self, review_query: str, *, max_results: int | None = None
    ) -> list[RepoReviewHit]:
        terms = query_terms(review_query)
        if not terms:
            return []

        self._sync_index()

        hits = await self.index.search(
            source_type="repo",
            query=review_query,
            max_hits=max_results or self.config.max_results,
            semantic_weight=self._semantic_weight,
        )
        return [self._to_hit(h, terms) for h in hits]

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
                "repo",
                review_query,
                limit=max_results or self.config.max_results,
            )
            chunks = self.index._load_chunks_by_keys(set(fts_scores.keys()))
            raw = []
            for key, score in sorted(fts_scores.items(), key=lambda x: -x[1]):
                chunk = chunks.get(key)
                if chunk:
                    from nanobot.rag.utils import IndexedHit
                    raw.append(IndexedHit(chunk=chunk, score=score, reason=["bm25"]))
            return [self._to_hit(h, terms) for h in raw[: max_results or self.config.max_results]]
        else:
            return asyncio.run(self.async_retrieve(review_query, max_results=max_results))

    def build_review_block(
        self,
        review_query: str,
        *,
        max_results: int | None = None,
        include_tests: bool | None = None,
    ) -> str:
        hits = self.retrieve(review_query, max_results=max_results)
        if not hits:
            return "No relevant repository review references found."

        return self._format_review_block(hits, include_tests=include_tests)

    def build_context_block(
        self,
        query: str,
        *,
        max_hits: int | None = None,
        include_tests: bool | None = None,
    ) -> str:
        """Backward-compatible wrapper for the old repo_context naming."""
        return self.build_review_block(
            query,
            max_results=max_hits,
            include_tests=include_tests,
        )

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
            if show_tests:
                related_tests = self._related_test_paths(hit.path)
                if related_tests:
                    parts.append(f"- likely related tests: {', '.join(related_tests[:5])}")
            if hit.snippet:
                parts.append("```text")
                parts.append(hit.snippet)
                parts.append("```")

        parts.append("[/Repository Review References]")
        return "\n".join(parts)

    def _format_context_block(
        self,
        hits: list[RepoReviewHit],
        *,
        include_tests: bool | None = None,
    ) -> str:
        """Backward-compatible wrapper for the old repo_context naming."""
        return self._format_review_block(hits, include_tests=include_tests)

    def _sync_index(self) -> None:
        from nanobot.rag.chunk_filter import should_skip_file_embedding

        self.index.sync_files(
            source_type="repo",
            files=self._iter_candidate_files(),
            chunker=self._chunk_file,
            max_file_chars=self.config.max_file_chars,
            skip_embed_filter=should_skip_file_embedding,
        )

    def _chunk_file(self, path: Path, text: str) -> list[IndexedChunk]:
        rel = path.relative_to(self.workspace).as_posix()
        suffix = path.suffix.lower()

        # Try Tree-sitter semantic chunking first
        if self._chunker.can_parse(suffix):
            chunks = self._chunker.chunk_file(rel, text, suffix, source_type="repo")
            if chunks:
                return chunks[: self.config.max_chunks_per_file]

        # Fallback: sliding window
        symbols = self._chunker.extract_symbols(text, suffix)
        return self._line_window_chunks(rel, text, symbols=symbols)

    def _iter_candidate_files(self) -> Iterable[Path]:
        count = 0
        for path in self.workspace.rglob("*"):
            if count >= self.config.max_files:
                return
            if not path.is_file():
                continue
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

        if any(part in self.config.ignore_dirs for part in rel_parts):
            return True
        if rel_parts[:3] == ("references", "web", "pages"):
            return True
        if rel_parts and rel_parts[0] == ".nanobot":
            return True

        return any(
            fnmatch.fnmatch(path.name, pattern)
            for pattern in self.config.ignore_globs
        )

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
                    source_type="repo",
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

    def __init__(self, config: GitHubRepoConfig | None = None) -> None:
        self.config = config or GitHubRepoConfig()
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
        return f"Error: unknown GitHub action '{action}'. Use 'meta', 'tree', or 'file'."

    async def _get_token(self) -> str | None:
        if self._token_cache:
            return self._token_cache
        token = self.config.token.strip() or os.environ.get("GITHUB_TOKEN", "").strip()
        if token:
            self._token_cache = token
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
            return f"Error: request to GitHub API timed out ({self.config.timeout}s)."
        except httpx.HTTPError as exc:
            return f"Error: HTTP request failed: {exc}"

        if response.status_code == 403:
            remaining = response.headers.get("X-RateLimit-Remaining", "")
            if remaining == "0":
                reset = response.headers.get("X-RateLimit-Reset", "unknown")
                auth_hint = " Set GITHUB_TOKEN for higher limits (5000 req/hr)." if not token else ""
                return f"Error: GitHub API rate limited. Resets at timestamp {reset}.{auth_hint}"
            return "Error: access denied (403). The repo may be private; ensure GITHUB_TOKEN is set."
        if response.status_code == 404:
            return "Error: repository or path not found. Check the URL and access permissions."
        if response.status_code >= 400:
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


@tool_parameters(
    tool_parameters_schema(
        target_type=StringSchema(
            "Review target type: 'local' for current workspace search or 'github' for GitHub API reads",
            enum=("local", "github"),
        ),
        action=StringSchema(
            "GitHub action when target_type='github': 'meta', 'tree', or 'file'",
            enum=("meta", "tree", "file"),
            nullable=True,
        ),
        target_repo=StringSchema(
            "GitHub repo in 'owner/repo' format or a full GitHub URL when target_type='github'",
            nullable=True,
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
        embedding_client = create_embedding_client_from_config(embedding_config)
        rerank_client = create_rerank_client_from_config(rerank_config)
        return cls(
            workspace=Path(ctx.workspace),
            embedding_client=embedding_client,
            rerank_client=rerank_client,
            github_config=getattr(ctx.config, "github_repo", None),
            semantic_weight=getattr(embedding_config, "semantic_weight", 0.6),
        )

    def __init__(
        self,
        workspace: Path,
        embedding_client: Any | None = None,
        rerank_client: Any | None = None,
        github_config: GitHubRepoConfig | None = None,
        semantic_weight: float = 0.6,
    ) -> None:
        config = RepoReviewRetrieverConfig(
            enable_semantic=embedding_client is not None,
        )
        self.retriever = LocalRepoReader(
            workspace,
            config=config,
            embedding_client=embedding_client,
            rerank_client=rerank_client,
            semantic_weight=semantic_weight,
        )
        self.github = GitHubRepoReader(github_config)

    @property
    def name(self) -> str:
        return "repo_review"

    @property
    def description(self) -> str:
        return (
            "Retrieve repository review references. For local workspace code, "
            "search likely relevant files, symbols, and snippets with review_query. "
            "For GitHub repos, use target_type='github' with action='meta', "
            "'tree', or 'file'."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        review_query: str | None = None,
        target_type: str = "local",
        action: str | None = None,
        target_repo: str | None = None,
        repo_path: str | None = None,
        ref: str | None = None,
        tree_pattern: str | None = None,
        max_results: int = 5,
        include_tests: bool | None = None,
        tree_limit: int = 500,
        **kwargs: Any,
    ) -> str:
        review_query = review_query or kwargs.get("query")
        target_repo = target_repo or kwargs.get("repo")
        repo_path = repo_path or kwargs.get("path")
        tree_pattern = tree_pattern or kwargs.get("pattern")
        max_results = int(kwargs.get("max_hits", max_results))
        tree_limit = int(kwargs.get("max_entries", tree_limit))
        if target_type == "local" and "source" in kwargs:
            target_type = kwargs["source"]

        if target_type == "github":
            if not self.github.config.enable:
                return "Error: GitHub repository access is disabled by tools.githubRepo.enable."
            if not target_repo or not target_repo.strip():
                return "Error: target_repo is required when target_type='github'."
            return await self.github.execute(
                action=action or "meta",
                repo=target_repo.strip(),
                path=repo_path,
                ref=ref,
                pattern=tree_pattern,
                max_entries=tree_limit,
            )

        if not review_query or not review_query.strip():
            return "Error: review_query is required."

        hits = await self.retriever.async_retrieve(
            review_query.strip(),
            max_results=max_results,
        )
        if not hits:
            return "No relevant repository review references found."
        return self.retriever._format_review_block(hits, include_tests=include_tests)


