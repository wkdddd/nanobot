"""Resolve local and GitHub repository targets for review mode."""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
import hashlib
import subprocess
import asyncio
_GITHUB_RE = re.compile(
    r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$"
)
@dataclass(frozen=True,slots=True)
class ReviewTarget:
    original:str
    path:Path
    kind:str
    display_name:str
def is_github_repo_url(value:str) ->bool:
    return bool(_GITHUB_RE.match(value.strip()))
async def resolve_review_target(target:str,workspace:Path) ->ReviewTarget:
    raw=target.strip()
    local=Path(raw).expanduser()
    if local.exists():
        return ReviewTarget(
            original=raw,
            path=local.resolve(),
            kind="local",
            display_name=local.resolve().name
        )
    if is_github_repo_url(raw):
        return _prepare_github_target(raw, workspace=workspace)
    raise ValueError(f"Review target is not a local path or supported GitHub repository URL: {target}")
async def _prepare_github_target(url:str,workspace:Path) ->ReviewTarget:
    match=_GITHUB_RE.match(url.strip())
    if not match:
        raise ValueError(f"Unsupported GitHub repository URL: {url}")
    owner=match.group("owner")
    repo = match.group("repo")
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
    clone_root=workspace/".nanobot"/"reviews"
    target_dir = clone_root / f"{owner}-{repo}-{digest}"
    clone_root.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        await _run_git(["git", "-C", str(target_dir), "pull", "--ff-only"])
    else:
        await _run_git(["git", "clone", "--depth", "1",
            "--config", "core.hooksPath=/dev/null",
            "--config", "core.fsmonitor=false",
            url, str(target_dir),])
        hooks_dir = target_dir / ".git" / "hooks"
        if hooks_dir.exists():
            for hook_file in hooks_dir.iterdir():
                hook_file.unlink(missing_ok=True)
    return ReviewTarget(
        original=url,
        path=target_dir.resolve(),
        kind="github",
        display_name=f"{owner}/{repo}",
    )

async def _run_git(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        if proc.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Git command failed: {detail}")



