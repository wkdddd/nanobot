"""Hard validation for review finding candidates."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from nanobot.agent.review.types import (
    SEVERITY_ORDER,
    FindingVerdict,
    ReviewDimensionResult,
    ReviewFindingCandidate,
    ReviewFindingVerdict,
)


@dataclass
class ValidationContext:
    """Context for validating candidates against a workspace."""

    workspace: str
    changed_files: list[str] = field(default_factory=list)
    max_line_lookup: bool = True
    local_target: str | None = None


class ReviewValidator:
    """Validates candidate findings with hard checks (no LLM)."""

    def __init__(self, ctx: ValidationContext) -> None:
        self._ctx = ctx
        self._workspace = Path(ctx.workspace).resolve()
        self._local_target = self._resolve_local_target(ctx.local_target)
        self._changed_files = {self._normalize_rel_path(path) for path in ctx.changed_files}
        self._seen_fingerprints: set[str] = set()
        self.stats = {"accepted": 0, "rejected": 0, "needs_confirmation": 0}

    def validate_candidates(
        self, candidates: list[ReviewFindingCandidate], dimension: str
    ) -> ReviewDimensionResult:
        result = ReviewDimensionResult(dimension=dimension, status="validated")
        for candidate in candidates:
            verdict = self._validate_one(candidate)
            if verdict.verdict == FindingVerdict.ACCEPTED:
                result.accepted.append(candidate)
                self.stats["accepted"] += 1
            elif verdict.verdict == FindingVerdict.REJECTED:
                result.rejected.append((candidate, verdict))
                self.stats["rejected"] += 1
            else:
                result.uncertain.append((candidate, verdict))
                self.stats["needs_confirmation"] += 1
        result.candidates = candidates
        logger.debug(
            "validator dimension={} accepted={} rejected={} needs_confirmation={}",
            dimension,
            len(result.accepted),
            len(result.rejected),
            len(result.uncertain),
        )
        return result

    def _validate_one(self, c: ReviewFindingCandidate) -> ReviewFindingVerdict:
        '''assign a single candidate finding with accepted/rejected/uncertain'''
        if c.severity not in SEVERITY_ORDER:
            return ReviewFindingVerdict(
                verdict=FindingVerdict.REJECTED,
                reason=f"invalid severity: {c.severity}",
            )
        if not c.file or not c.title:
            return ReviewFindingVerdict(
                verdict=FindingVerdict.REJECTED,
                reason="missing required field (file or title)",
            )
        fp = self._fingerprint(c)
        if fp in self._seen_fingerprints:
            return ReviewFindingVerdict(
                verdict=FindingVerdict.REJECTED, reason="duplicate finding"
            )
        self._seen_fingerprints.add(fp)

        file_path = self._resolve_candidate_path(c.file)
        if file_path is None:
            boundary = "target" if self._local_target is not None else "workspace"
            return ReviewFindingVerdict(
                verdict=FindingVerdict.REJECTED,
                reason=f"path outside {boundary}: {c.file}",
            )
        if not file_path.is_file():
            return ReviewFindingVerdict(
                verdict=FindingVerdict.REJECTED,
                reason=f"file not found: {c.file}",
            )
        if c.line is not None and self._ctx.max_line_lookup:
            if not self._line_in_range(file_path, c.line):
                return ReviewFindingVerdict(
                    verdict=FindingVerdict.REJECTED,
                    reason=f"line {c.line} out of range for {c.file}",
                )
        normalized_file = self._workspace_relative_path(file_path)
        if self._changed_files and normalized_file not in self._changed_files:
            return ReviewFindingVerdict(
                verdict=FindingVerdict.UNCERTAIN,
                reason="file not in changed set",
                missing_evidence="file is outside PR/diff scope",
            )
        if not c.evidence.strip():
            return ReviewFindingVerdict(
                verdict=FindingVerdict.UNCERTAIN,
                reason="no evidence provided",
                missing_evidence="candidate lacks supporting evidence snippet",
            )
        if not self._evidence_matches(file_path, c.evidence, c.line):
            return ReviewFindingVerdict(
                verdict=FindingVerdict.UNCERTAIN,
                reason="evidence not found in file",
                missing_evidence="candidate evidence snippet does not match the target file",
            )
        return ReviewFindingVerdict(verdict=FindingVerdict.ACCEPTED)

    def _fingerprint(self, c: ReviewFindingCandidate) -> str:
        key = f"{c.file}:{c.line or 0}:{c.title.lower().strip()}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _line_in_range(self, path: Path, line: int) -> bool:
        if line < 1:
            return False
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                count = sum(1 for _ in f)
            return line <= count
        except OSError:
            return False

    @staticmethod
    def _resolve_local_target(raw: str | None) -> Path | None:
        if not raw or not str(raw).strip():
            return None
        try:
            return Path(str(raw)).expanduser().resolve()
        except OSError:
            return None

    def _resolve_candidate_path(self, raw: str) -> Path | None:
        value = raw.strip()
        if not value:
            return None
        candidate = Path(value)
        if candidate.is_absolute():
            if self._local_target is None:
                return None
            try:
                resolved = candidate.expanduser().resolve()
            except OSError:
                return None
            return resolved if self._is_under_allowed_target(resolved) else None
        try:
            resolved = (self._workspace / candidate).resolve()
        except (OSError, ValueError):
            return None
        if not self._is_under_allowed_target(resolved):
            return None
        return resolved

    def _is_under_allowed_target(self, resolved: Path) -> bool:
        if self._local_target is not None:
            target = self._local_target
            if not target.exists():
                return False
            if target.is_file():
                return resolved == target
            try:
                resolved.relative_to(target)
                return True
            except ValueError:
                return False
        try:
            resolved.relative_to(self._workspace)
            return True
        except ValueError:
            return False

    @staticmethod
    def _normalize_rel_path(raw: str) -> str:
        return raw.replace("\\", "/").strip().lstrip("./")

    def _workspace_relative_path(self, path: Path) -> str:
        try:
            return path.relative_to(self._workspace).as_posix()
        except ValueError:
            return self._normalize_rel_path(str(path))

    @classmethod
    def _evidence_matches(cls, path: Path, evidence: str, line: int | None = None) -> bool:
        snippets = cls._evidence_snippets(evidence)
        if not snippets:
            return False
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        normalized_text = " ".join(text.split())
        if any(snippet in normalized_text for snippet in snippets):
            return True
        if line is None:
            return False
        lines = text.splitlines()
        start = max(line - 4, 0)
        end = min(line + 3, len(lines))
        nearby = " ".join(" ".join(item.split()) for item in lines[start:end])
        return any(snippet in nearby for snippet in snippets)

    @staticmethod
    def _evidence_snippets(evidence: str) -> list[str]:
        raw = evidence.strip()
        if not raw:
            return []
        candidates: list[str] = []
        candidates.extend(match.strip() for match in re.findall(r"`([^`\n]+)`", raw))
        cleaned = re.sub(r"^\s*(?:line|lines)\s+\d+(?:\s*[-:]\s*\d+)?\s*[:：-]\s*", "", raw, flags=re.I)
        cleaned = re.sub(r"^\s*[-*+>]\s*", "", cleaned)
        candidates.append(cleaned)
        snippets: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            snippet = " ".join(candidate.split()).strip()
            if not snippet or snippet in seen:
                continue
            seen.add(snippet)
            snippets.append(snippet)
        return snippets
