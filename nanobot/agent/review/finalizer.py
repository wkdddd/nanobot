"""Review finalizer: parse subagent results, validate, orchestrate semantic review."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from loguru import logger

from nanobot.agent.review.report import render_review_report
from nanobot.agent.review.types import (
    FindingVerdict,
    ReviewDimensionResult,
    ReviewFindingCandidate,
    ReviewFindingVerdict,
)
from nanobot.agent.review.validator import ReviewValidator, ValidationContext


@dataclass
class ReviewFinalizerResult:
    """Output of the finalizer process."""

    report_markdown: str
    dimensions: list[ReviewDimensionResult] = field(default_factory=list)
    needs_review: list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]] = field(
        default_factory=list
    )
    errors: list[str] = field(default_factory=list)


class ReviewFinalizer:
    """Parses subagent outputs, validates findings, produces final report."""

    def __init__(self, workspace: str, changed_files: list[str] | None = None) -> None:
        self._ctx = ValidationContext(
            workspace=workspace,
            changed_files=changed_files or [],
        )
        self._validator = ReviewValidator(self._ctx)
        self._dimensions: list[ReviewDimensionResult] = []
        self._errors: list[str] = []

    def ingest_subagent_output(self, dimension: str, raw_output: str) -> ReviewDimensionResult:
        """Parse one subagent's raw text output and validate its candidates."""
        candidates = self._parse_candidates(dimension, raw_output)
        if not candidates:
            result = ReviewDimensionResult(dimension=dimension, status="no_findings")
            self._dimensions.append(result)
            return result
        result = self._validator.validate_candidates(candidates, dimension)
        self._dimensions.append(result)
        return result

    def get_needs_review(self) -> list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]]:
        """Return all uncertain candidates across dimensions that need semantic review."""
        items: list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]] = []
        for d in self._dimensions:
            items.extend(d.uncertain)
        return items

    def apply_semantic_verdicts(
        self, verdicts: list[tuple[ReviewFindingCandidate, FindingVerdict]]
    ) -> None:
        """Apply main agent's semantic review verdicts to uncertain items."""
        verdict_map: dict[str, FindingVerdict] = {}
        for candidate, v in verdicts:
            key = f"{candidate.file}:{candidate.line}:{candidate.title}"
            verdict_map[key] = v

        for dim in self._dimensions:
            still_uncertain: list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]] = []
            for candidate, orig_verdict in dim.uncertain:
                key = f"{candidate.file}:{candidate.line}:{candidate.title}"
                final = verdict_map.get(key)
                if final == FindingVerdict.ACCEPTED:
                    dim.accepted.append(candidate)
                elif final == FindingVerdict.REJECTED:
                    dim.rejected.append(
                        (candidate, ReviewFindingVerdict(
                            verdict=FindingVerdict.REJECTED, reason="rejected by semantic review"
                        ))
                    )
                else:
                    still_uncertain.append((candidate, orig_verdict))
            dim.uncertain = still_uncertain

    def finalize(self, target_name: str) -> ReviewFinalizerResult:
        """Produce final report markdown from all ingested dimensions."""
        needs_review = self.get_needs_review()
        try:
            report = render_review_report(target_name, self._dimensions)
        except Exception as exc:
            logger.error("report rendering failed: {}", exc)
            report = f"## Code Review Report: {target_name}\n\n### Error\n\nReport rendering failed: {exc}\n"
            self._errors.append(str(exc))

        return ReviewFinalizerResult(
            report_markdown=report,
            dimensions=self._dimensions,
            needs_review=needs_review,
            errors=self._errors,
        )

    def _parse_candidates(
        self, dimension: str, raw: str
    ) -> list[ReviewFindingCandidate]:
        """Try JSON array first, fall back to JSONL, then markdown table."""
        candidates = self._try_parse_json_array(dimension, raw)
        if candidates:
            return candidates
        candidates = self._try_parse_jsonl(dimension, raw)
        if candidates:
            return candidates
        candidates = self._try_parse_markdown_table(dimension, raw)
        return candidates

    def _try_parse_json_array(
        self, dimension: str, raw: str
    ) -> list[ReviewFindingCandidate]:
        match = re.search(r"\[[\s\S]*\]", raw)
        if not match:
            return []
        try:
            data = json.loads(match.group())
            if not isinstance(data, list):
                return []
            return [self._dict_to_candidate(d, dimension) for d in data if isinstance(d, dict)]
        except (json.JSONDecodeError, TypeError, KeyError):
            return []

    def _try_parse_jsonl(
        self, dimension: str, raw: str
    ) -> list[ReviewFindingCandidate]:
        results: list[ReviewFindingCandidate] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
                if isinstance(d, dict) and "title" in d:
                    results.append(self._dict_to_candidate(d, dimension))
            except (json.JSONDecodeError, TypeError, KeyError):
                continue
        return results

    def _try_parse_markdown_table(
        self, dimension: str, raw: str
    ) -> list[ReviewFindingCandidate]:
        """Parse markdown table rows with columns: severity | file | line | title | evidence | impact | recommendation."""
        results: list[ReviewFindingCandidate] = []
        table_row_re = re.compile(
            r"^\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|\s*([^|]*)\s*\|\s*([^|]+)\s*\|\s*([^|]*)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|$"
        )
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("|---") or line.startswith("| ---"):
                continue
            m = table_row_re.match(line)
            if not m:
                continue
            sev, file, line_no, title, evidence, impact, rec = (
                g.strip() for g in m.groups()
            )
            if sev.lower() in ("severity", "#", "no"):
                continue
            try:
                ln = int(line_no) if line_no else None
            except ValueError:
                ln = None
            results.append(ReviewFindingCandidate(
                severity=sev.lower(),
                dimension=dimension,
                file=file,
                line=ln,
                title=title,
                evidence=evidence,
                impact=impact,
                recommendation=rec,
            ))
        return results

    def _dict_to_candidate(self, d: dict, dimension: str) -> ReviewFindingCandidate:
        return ReviewFindingCandidate(
            severity=str(d.get("severity", "medium")).lower(),
            dimension=dimension,
            file=str(d.get("file", "")),
            line=d.get("line"),
            title=str(d.get("title", "")),
            evidence=str(d.get("evidence", "")),
            impact=str(d.get("impact", "")),
            recommendation=str(d.get("recommendation", "")),
            confidence=str(d.get("confidence", "high")),
            source=str(d.get("source", "")),
        )
