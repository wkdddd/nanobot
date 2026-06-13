"""Code review role definitions."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True,slots=True)
class ReviewRole:
    name:str
    label:str
    description:str

DEFAULT_REVIEW_ROLES:dict[str,ReviewRole]={
    "security":ReviewRole(
        name="security",
        label="Security Reviewer",
        description=("Review authentication, authorization, injection risks, secret handling, "
            "unsafe deserialization, path traversal, SSRF, dependency risks, and data exposure."
        )
    ),
    "tests": ReviewRole(
        name="tests",
        label="Test Reviewer",
        description=(
            "Review test coverage, missing edge cases, brittle tests, regression risk, "
            "testability, and suggested verification commands."
        ),
    ),
    "architecture": ReviewRole(
        name="architecture",
        label="Architecture Reviewer",
        description=(
            "Review module boundaries, coupling, maintainability, data flow, abstractions, "
            "configuration shape, and long-term design risks."
        ),
    ),
    "performance": ReviewRole(
        name="performance",
        label="Performance Reviewer",
        description=(
            "Review algorithmic complexity, I/O hot paths, concurrency, caching, memory use, "
            "database/query behavior, and scalability risks."
        ),
    ),
}
def normalize_focus(raw:str|None) ->tuple[list[ReviewRole],bool]:
    forced_flag:bool=True
    if not raw:
        forced_flag=False
        return list(DEFAULT_REVIEW_ROLES.values()),forced_flag
    
    selected:list[ReviewRole]=[]
    for item in raw.split(","):
        key=item.strip().lower()
        if not key:
            continue
        role = DEFAULT_REVIEW_ROLES.get(key)
        if role is None:
            allowed = ", ".join(sorted(DEFAULT_REVIEW_ROLES))
            raise ValueError(f"Unknown review focus '{key}'. Available focus values: {allowed}")
        if role and role not in selected:
            selected.append(role)

    return selected or list(DEFAULT_REVIEW_ROLES.values()),forced_flag
