from __future__ import annotations

from nanobot.config.schema import Config


def test_review_token_budget_fields_accept_camel_case() -> None:
    config = Config(
        review={
            "tokenBudget": 120_000,
            "prefetchBudgetChars": 12_000,
            "subagentEvidenceBudgetChars": 18_000,
        }
    )

    assert config.review.token_budget == 120_000
    assert config.review.prefetch_budget_chars == 12_000
    assert config.review.subagent_evidence_budget_chars == 18_000
