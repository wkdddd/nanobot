"""Review finalizer: parse subagent results, validate, and render reports."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from nanobot.agent.lifecycle_hook import AgentHook, AgentHookContext
from nanobot.agent.review.report import render_review_report
from nanobot.agent.review.types import (
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
    needs_confirmation: list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]] = field(
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

    @property
    def dimensions(self) -> list[ReviewDimensionResult]:
        return list(self._dimensions)

    def ingest_messages(self, messages: list[dict[str, Any]]) -> int:
        """Ingest structured subagent outputs from runner messages."""
        count = 0
        for message in messages:
            meta = self._subagent_metadata(message)
            if not meta:
                continue
            dimension = str(meta.get("subagent_label") or meta.get("label") or "unknown")
            raw_output = self._subagent_raw_output(message, meta)
            if not raw_output.strip():
                logger.warning("review.finalizer.skip_empty dimension={}", dimension)
                continue
            self.ingest_subagent_output(dimension, raw_output)
            count += 1
        logger.info("review.finalizer.ingest messages={} dimensions={}", len(messages), count)
        return count

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

    def get_needs_confirmation(self) -> list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]]:
        """Return uncertain candidates that should be shown separately in the report."""
        items: list[tuple[ReviewFindingCandidate, ReviewFindingVerdict]] = []
        for d in self._dimensions:
            items.extend(d.uncertain)
        return items

    def finalize(self, target_name: str) -> ReviewFinalizerResult:
        """Produce final report markdown from all ingested dimensions."""
        needs_confirmation = self.get_needs_confirmation()
        try:
            report = render_review_report(target_name, self._dimensions)
        except Exception as exc:
            logger.error("report rendering failed: {}", exc)
            report = f"## Code Review Report: {target_name}\n\n### Error\n\nReport rendering failed: {exc}\n"
            self._errors.append(str(exc))

        return ReviewFinalizerResult(
            report_markdown=report,
            dimensions=self._dimensions,
            needs_confirmation=needs_confirmation,
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
        for candidate_text in self._json_candidate_texts(raw):
            try:
                data = json.loads(candidate_text)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, list):
                return [self._dict_to_candidate(d, dimension) for d in data if isinstance(d, dict)]
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

    @staticmethod
    def _subagent_metadata(message: dict[str, Any]) -> dict[str, Any] | None:
        meta = message.get("_metadata")
        if not isinstance(meta, dict):
            meta = message.get("metadata")
        if not isinstance(meta, dict):
            meta = {}
        if message.get("injected_event") == "subagent_result":
            meta = {
                **meta,
                **{
                    key: message[key]
                    for key in (
                        "injected_event",
                        "subagent_task_id",
                        "subagent_label",
                        "subagent_status",
                        "subagent_result",
                    )
                    if key in message
                },
            }
        if meta.get("injected_event") != "subagent_result":
            return None
        return meta

    @staticmethod
    def _subagent_raw_output(message: dict[str, Any], meta: dict[str, Any]) -> str:
        raw = meta.get("subagent_result")
        if isinstance(raw, str):
            return raw
        content = message.get("content", "")
        if not isinstance(content, str):
            return str(content)
        if "Result:" in content:
            content = content.split("Result:", 1)[1]
        if "Summarize this naturally" in content:
            content = content.split("Summarize this naturally", 1)[0]
        return content.strip()

    @classmethod
    def _json_candidate_texts(cls, raw: str) -> list[str]:
        texts: list[str] = []
        for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE):
            texts.append(match.group(1).strip())
        body = raw.strip()
        if "Result:" in body:
            body = body.split("Result:", 1)[1].strip()
        texts.append(body)
        texts.extend(cls._balanced_json_arrays(body))
        return [text for text in texts if text]

    @staticmethod
    def _balanced_json_arrays(raw: str) -> list[str]:
        decoder = json.JSONDecoder()
        arrays: list[str] = []
        for idx, ch in enumerate(raw):
            if ch != "[":
                continue
            try:
                parsed, end = decoder.raw_decode(raw[idx:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                arrays.append(raw[idx : idx + end])
        return arrays


class ReviewFinalizerHook(AgentHook):
    """Runner hook that renders a fixed review report from subagent outputs."""

    def __init__(
        self,
        *,
        workspace: str,
        target_name: str,
        changed_files: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._target_name = target_name
        self._finalizer = ReviewFinalizer(workspace, changed_files)
        self._rendered = False

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        if self._rendered:
            return content
        ingested = self._finalizer.ingest_messages(context.messages)
        if ingested == 0:
            logger.warning("review.finalizer.no_subagent_results target={}", self._target_name)
            return content
        result = self._finalizer.finalize(self._target_name)
        self._rendered = True
        context.content_replaced = True
        logger.info(
            "review.finalizer.rendered target={} dimensions={} needs_confirmation={} errors={}",
            self._target_name,
            len(result.dimensions),
            len(result.needs_confirmation),
            len(result.errors),
        )
        return result.report_markdown
