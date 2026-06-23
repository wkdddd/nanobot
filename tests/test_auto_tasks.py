import hashlib
import hmac

import pytest

from nanobot.auto_tasks.github import parse_pull_request_event, verify_github_signature
from nanobot.auto_tasks.service import AutoTaskService
from nanobot.auto_tasks.store import AutoTaskStore
from nanobot.config.schema import Config
from nanobot.session.manager import SessionManager


def _payload(repo: str = "owner/repo", number: int = 12) -> dict:
    return {
        "action": "synchronize",
        "repository": {"full_name": repo},
        "pull_request": {
            "number": number,
            "title": "Update code",
            "html_url": f"https://github.com/{repo}/pull/{number}",
            "draft": False,
        },
    }


def test_github_signature_validation() -> None:
    secret = "top-secret"
    body = b'{"ok":true}'
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    assert verify_github_signature(secret=secret, body=body, signature=f"sha256={digest}")
    assert not verify_github_signature(secret=secret, body=body, signature="sha256=bad")
    assert not verify_github_signature(secret="", body=body, signature=f"sha256={digest}")


def test_parse_pull_request_event_normalizes_repo() -> None:
    event = parse_pull_request_event(_payload("Owner/Repo"))

    assert event.repo == "Owner/Repo"
    assert event.pr_number == 12
    assert event.pr_url == "https://github.com/Owner/Repo/pull/12"


def test_store_crud_and_runs(tmp_path) -> None:
    store = AutoTaskStore(tmp_path / "tasks.json")
    task = store.create_task({"name": "Review PRs", "repo": "owner/repo", "mode": "full"})

    assert store.get_task(task.id) is not None
    updated = store.update_task(task.id, {"enabled": False})
    assert updated is not None
    assert updated.enabled is False
    assert store.delete_task(task.id) is True
    assert store.get_task(task.id) is None


def test_store_treats_malformed_empty_state_as_empty_lists(tmp_path) -> None:
    path = tmp_path / "tasks.json"
    path.write_text("null", encoding="utf-8")
    store = AutoTaskStore(path)

    assert store.list_tasks() == []
    assert store.list_runs() == []

    path.write_text("[]", encoding="utf-8")
    assert store.list_tasks() == []
    assert store.list_runs() == []

    path.write_text('{"tasks": null, "runs": null}', encoding="utf-8")
    assert store.list_tasks() == []
    assert store.list_runs() == []


def test_session_list_includes_safe_auto_task_metadata(tmp_path) -> None:
    manager = SessionManager(tmp_path)
    session = manager.get_or_create("websocket:auto-task-chat")
    session.metadata.update(
        {
            "auto_task_id": "task-1",
            "auto_task_run_id": "run-1",
            "github_repo": "owner/repo",
            "github_pr_number": 12,
            "review_target": "https://github.com/owner/repo/pull/12",
            "review_target_type": "github",
            "review_action": "diff",
            "review_mode_variant": "deep",
            "github_token": "secret",
            "permissions": {"approval": True},
        }
    )
    manager.save(session)

    rows = manager.list_sessions()
    row = next(item for item in rows if item["key"] == "websocket:auto-task-chat")

    assert row["metadata"] == {
        "auto_task_id": "task-1",
        "auto_task_run_id": "run-1",
        "github_repo": "owner/repo",
        "github_pr_number": 12,
        "review_target": "https://github.com/owner/repo/pull/12",
        "review_target_type": "github",
        "review_action": "diff",
        "review_mode_variant": "deep",
    }


@pytest.mark.asyncio
async def test_service_triggers_matching_task(tmp_path) -> None:
    calls = []

    async def start_review(payload: dict) -> dict[str, str]:
        calls.append(payload)
        return {"chat_id": "chat", "session_key": "websocket:chat"}

    config = Config()
    store = AutoTaskStore(tmp_path / "tasks.json")
    task = store.create_task({"name": "Review PRs", "repo": "owner/repo", "mode": "quick"})
    service = AutoTaskService(config, store, review_starter=start_review)

    runs = await service.trigger_for_event(parse_pull_request_event(_payload()))

    assert len(runs) == 1
    assert runs[0].task_id == task.id
    assert calls[0]["target"] == "https://github.com/owner/repo/pull/12"
    assert calls[0]["action"] == "diff"
    assert calls[0]["mode"] == "quick"
