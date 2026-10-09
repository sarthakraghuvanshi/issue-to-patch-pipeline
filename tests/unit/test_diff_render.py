"""render_diff_html: unified-diff parsing, line classification, escaping."""

from __future__ import annotations

from issue_to_patch.api.diff_render import render_diff_html

_SIMPLE_DIFF = """\
From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001
From: issue-to-patch <bot@issue-to-patch.local>
Date: Wed, 1 Jan 2020 00:00:00 +0000
Subject: [PATCH] fix

---
 calculator.py | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)

diff --git a/calculator.py b/calculator.py
index 325e6d8..4693ad3 100644
--- a/calculator.py
+++ b/calculator.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""


def test_renders_one_block_per_file_with_header() -> None:
    out = render_diff_html(_SIMPLE_DIFF)
    assert "calculator.py" in out
    assert out.count("<div class='file-header'>") == 1


def test_classifies_added_and_removed_and_context_lines() -> None:
    out = render_diff_html(_SIMPLE_DIFF)
    assert "class='code add'" in out
    assert "class='code del'" in out
    assert "<tr class='ctx'>" in out
    assert "return a + b" in out
    assert "return a - b" in out


def test_line_numbers_track_old_and_new_separately() -> None:
    out = render_diff_html(_SIMPLE_DIFF)
    # the context line "def add(a, b):" is line 1 in both old and new
    assert out.count("<td class='ln ctx'>1</td>") == 2
    # the removed line is old line 2, the added line is new line 2
    assert "<td class='ln del'>2</td>" in out
    assert "<td class='ln add'>2</td>" in out


def test_code_content_is_html_escaped() -> None:
    dangerous = """\
diff --git a/x.py b/x.py
index 0000000..1111111 100644
--- a/x.py
+++ b/x.py
@@ -1,1 +1,1 @@
-if a < b and "x" & y:
+if a < b and c & d:  # <script>alert(1)</script>
"""
    out = render_diff_html(dangerous)
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out
    assert "a &lt; b" in out


def test_multiple_files_each_get_their_own_block() -> None:
    two_files = """\
diff --git a/a.py b/a.py
index 0000000..1111111 100644
--- a/a.py
+++ b/a.py
@@ -1,1 +1,1 @@
-x = 1
+x = 2
diff --git a/b.py b/b.py
index 0000000..2222222 100644
--- a/b.py
+++ b/b.py
@@ -1,1 +1,1 @@
-y = 1
+y = 2
"""
    out = render_diff_html(two_files)
    assert out.count("<div class='file-header'>") == 2
    assert "a.py" in out
    assert "b.py" in out


def test_empty_patch_renders_a_placeholder_without_crashing() -> None:
    out = render_diff_html("")
    assert "empty diff" in out


def test_highlights_changed_tokens_across_reformatted_json() -> None:
    patch = """diff --git a/data.json b/data.json
--- a/data.json
+++ b/data.json
@@ -1,3 +1 @@
-{
-  "body": "old value"
-}
+{"body":"new value"}
"""
    out = render_diff_html(patch)
    assert "<mark>old</mark>" in out
    assert "<mark>new</mark>" in out
    assert "+1 added" in out
    assert "&minus;3 removed" in out
    assert "white-space: pre-wrap" in out


def test_multiple_files_have_navigation_and_collapsible_sections() -> None:
    out = render_diff_html(
        _SIMPLE_DIFF
        + """
diff --git a/other.py b/other.py
--- a/other.py
+++ b/other.py
@@ -10 +10 @@
-before
+after
"""
    )
    assert "2 changed files" in out
    assert "href='#diff-file-0'" in out
    assert "href='#diff-file-1'" in out
    assert "id='diff-file-1'" in out
    assert out.count("<details open>") == 2
    assert "@@ -10 +10 @@" in out
    assert "<td class='ln del'>10</td>" in out


def test_content_starting_with_diff_header_characters_is_preserved() -> None:
    out = render_diff_html("""diff --git a/x.txt b/x.txt
--- a/x.txt
+++ b/x.txt
@@ -1 +1 @@
--- content
+++ replacement
""")
    assert "-- content" in out
    assert "++ replacement" in out
    assert "+1 added" in out
    assert "&minus;1 removed" in out


def test_split_view_pairs_replacements_and_pads_unmatched_lines() -> None:
    out = render_diff_html("""diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1,3 +1,2 @@
-old first
-old second
+new first
 unchanged
""")
    assert "Current version</th>" in out
    assert "Modified version</th>" in out
    rows = out.split("<tr class='change'>")[1:]
    first = rows[0].split("</tr>")[0]
    assert "class='code del'" in first and "class='code add'" in first
    assert first.index("old first") < first.index("new first")
    second = rows[1].split("</tr>")[0]
    assert "old second" in second
    assert "class='code blank'" in second
    assert "<td class='ln ctx'>3</td>" in out
    assert "<td class='ln ctx'>2</td>" in out


def test_split_view_insertion_has_empty_current_side() -> None:
    out = render_diff_html("""diff --git a/new.py b/new.py
--- /dev/null
+++ b/new.py
@@ -0,0 +1 @@
+hello
""")
    row = out.split("<tr class='change'>")[1].split("</tr>")[0]
    assert row.startswith("<td class='ln blank'></td><td class='code blank'></td>")
    assert "<td class='ln add'>1</td>" in row
