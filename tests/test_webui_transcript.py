from nanobot.utils.webui_transcript import replay_transcript_to_ui_messages


def test_replay_uses_persisted_created_at() -> None:
    messages = replay_transcript_to_ui_messages([
        {"event": "user", "text": "hello", "createdAt": 1_700_000_000_000},
        {"event": "message", "text": "hi", "createdAt": 1_700_000_001_000},
    ])

    assert [m["createdAt"] for m in messages] == [1_700_000_000_000, 1_700_000_001_000]
    assert [m["role"] for m in messages] == ["user", "assistant"]


def test_replay_falls_back_for_legacy_records_without_created_at() -> None:
    messages = replay_transcript_to_ui_messages([
        {"event": "user", "text": "legacy"},
        {"event": "message", "text": "reply"},
    ])

    assert len(messages) == 2
    assert all(isinstance(m.get("createdAt"), int) and m["createdAt"] > 0 for m in messages)
    assert messages[0]["createdAt"] < messages[1]["createdAt"]


def test_replay_preserves_multi_turn_created_at_order() -> None:
    messages = replay_transcript_to_ui_messages([
        {"event": "user", "text": "one", "createdAt": 100},
        {"event": "delta", "text": "a", "createdAt": 110},
        {"event": "stream_end", "createdAt": 120},
        {"event": "turn_end", "createdAt": 130},
        {"event": "user", "text": "two", "createdAt": 200},
        {"event": "message", "text": "b", "createdAt": 210},
    ])

    assert [(m["role"], m["createdAt"]) for m in messages] == [
        ("user", 100),
        ("assistant", 110),
        ("user", 200),
        ("assistant", 210),
    ]


def test_replay_preserves_user_review_reference() -> None:
    messages = replay_transcript_to_ui_messages([
        {
            "event": "user",
            "text": "",
            "createdAt": 100,
            "review": {
                "mode": "deep",
                "target_type": "github",
                "target": "https://github.com/test/repo",
            },
        },
    ])

    assert messages[0]["review"] == {
        "mode": "deep",
        "target_type": "github",
        "target": "https://github.com/test/repo",
    }
