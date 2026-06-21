from nanobot.agent.review.policy import policy_for_depth


def test_quick_policy_is_high_risk_only() -> None:
    policy = policy_for_depth("quick", requested_max_subagents=6)

    assert policy.max_subagents == 2
    assert policy.severities == ("critical", "high")
    assert policy.judge_enabled is False
    assert [role.name for role in policy.roles] == ["security", "bug-risk", "tests"]


def test_full_policy_enables_judge_and_default_roles() -> None:
    policy = policy_for_depth("full", requested_max_subagents=4)

    assert policy.max_subagents == 4
    assert policy.judge_enabled is True
    assert [role.name for role in policy.roles] == [
        "security",
        "tests",
        "architecture",
        "performance",
    ]


def test_deep_policy_adds_optional_roles_and_more_capacity() -> None:
    policy = policy_for_depth("deep", requested_max_subagents=4)

    assert policy.max_subagents == 6
    assert policy.include_optional_roles is True
    assert "dependency" in {role.name for role in policy.roles}
