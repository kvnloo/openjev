"""Bounded next-action families (z0int RESEARCH.md §16)."""

from __future__ import annotations

FAMILIES = (
    "READ_SEARCH",
    "EDIT",
    "EXECUTE",
    "WEB",
    "DELEGATE",
    "VERIFY",
    "RESPOND",
    "ABSTAIN",
)

_PREFIX = (
    (("read", "search", "glob", "grep", "ls", "find", "codebase", "skill_view", "session_search"), "READ_SEARCH"),
    (("write", "patch", "edit", "apply"), "EDIT"),
    (("terminal", "bash", "execute", "process", "shell"), "EXECUTE"),
    (("web_", "browser_", "http"), "WEB"),
    (("delegate", "task", "kanban", "todo"), "DELEGATE"),
    (("vision", "video", "browser_vision"), "WEB"),
)


def family_of(tool_name: str | None) -> str:
    if not tool_name:
        return "RESPOND"
    n = tool_name.lower()
    if n in {"memory", "web_search", "web_extract"}:
        return "WEB" if n.startswith("web") else "READ_SEARCH"
    if "verify" in n or "test" in n or n == "browser_console":
        return "VERIFY"
    for prefixes, fam in _PREFIX:
        if any(n.startswith(p) or p in n for p in prefixes):
            return fam
    return "ABSTAIN"
