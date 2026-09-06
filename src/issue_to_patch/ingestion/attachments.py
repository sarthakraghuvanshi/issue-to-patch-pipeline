"""Issue attachments (screenshots, pasted images).

The learning plan maps the architecture's "images" box to issue attachments —
error screenshots, UI bug shots. Turning those into text needs a vision model,
which is a Sprint 5+ concern. For now: extract the URLs and record them, and
describe them through a processor that defaults to a no-op.
"""

from __future__ import annotations

import re
from typing import Protocol

_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((?P<url>[^)\s]+)")
_HTML_IMAGE_RE = re.compile(r"<img[^>]+src=[\"'](?P<url>[^\"']+)")
_ATTACHMENT_HOST_HINT = ("user-images.githubusercontent.com", "github.com/user-attachments")


def extract_attachment_urls(markdown: str) -> list[str]:
    """Return image URLs referenced in an issue or comment body, in order, de-duplicated."""
    seen: dict[str, None] = {}
    for pattern in (_MARKDOWN_IMAGE_RE, _HTML_IMAGE_RE):
        for match in pattern.finditer(markdown or ""):
            seen.setdefault(match.group("url"), None)
    return list(seen)


def looks_like_github_attachment(url: str) -> bool:
    return any(hint in url for hint in _ATTACHMENT_HOST_HINT)


class AttachmentDescriber(Protocol):
    async def describe(self, url: str) -> str: ...


class NullAttachmentDescriber:
    """Records nothing useful yet; keeps the interface stable for Sprint 5+."""

    async def describe(self, url: str) -> str:
        return ""
