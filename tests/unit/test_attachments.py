"""extract_attachment_urls: pull image links out of issue markdown."""

from __future__ import annotations

from issue_to_patch.ingestion.attachments import (
    extract_attachment_urls,
    looks_like_github_attachment,
)


def test_extracts_markdown_and_html_images_in_order_without_dupes() -> None:
    body = (
        "Steps:\n"
        "![screenshot](https://user-images.githubusercontent.com/1/a.png)\n"
        'and <img src="https://example.com/b.jpg" width="200">\n'
        "again ![same](https://user-images.githubusercontent.com/1/a.png)\n"
    )
    urls = extract_attachment_urls(body)
    assert urls == [
        "https://user-images.githubusercontent.com/1/a.png",
        "https://example.com/b.jpg",
    ]


def test_empty_body_is_safe() -> None:
    assert extract_attachment_urls("") == []


def test_github_attachment_host_detection() -> None:
    assert looks_like_github_attachment("https://user-images.githubusercontent.com/1/a.png")
    assert not looks_like_github_attachment("https://example.com/b.jpg")
