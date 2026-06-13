from __future__ import annotations

import pytest

from nanobot.agent.review.roles import normalize_focus


def test_normalize_focus_defaults_to_all_roles_without_forcing() -> None:
    roles, forced = normalize_focus(None)

    assert [role.name for role in roles] == ["security", "tests", "architecture", "performance"]
    assert forced is False


def test_normalize_focus_selects_requested_roles_and_deduplicates() -> None:
    roles, forced = normalize_focus("security, tests, security")

    assert [role.name for role in roles] == ["security", "tests"]
    assert forced is True


def test_normalize_focus_rejects_unknown_focus() -> None:
    with pytest.raises(ValueError, match="Unknown review focus 'bogus'"):
        normalize_focus("security,bogus")
