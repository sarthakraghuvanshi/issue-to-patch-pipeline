"""The browser-facing half of the API: a run list and a per-run review page.

Pure presentation over the existing JSON API/graph state — no new
orchestration logic, nothing here changes what a run means or how it's
validated. Self-contained HTML, no templating library (matches
``evaluation/run_suite.py``'s convention); excluded from the OpenAPI schema
since these routes return HTML, not JSON.

The page shell is deliberately NOT behind ``require_auth`` (see
``api/deps.py``) — there's no login/session/cookie anywhere in this
codebase to gate it with, so a bearer-token-protected page would simply be
unreachable by plain navigation. Only the JSON calls the page's own
JavaScript makes carry the token (read from a field the browser remembers
in ``localStorage``), when one is configured.
"""

from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from issue_to_patch.api import pending_runs
from issue_to_patch.api._common import branch_target
from issue_to_patch.api.deps import CheckpointerDep, GraphDepsDep, SettingsDep, StoreDep
from issue_to_patch.api.diff_render import render_diff_html
from issue_to_patch.graph import UnknownRun, load_investigation
from issue_to_patch.graph.state import InvestigationState
from issue_to_patch.ingestion import GitHubAPIError, GitHubClient, GitHubNotFound
from issue_to_patch.ingestion.fetch import list_issue_page
from issue_to_patch.ingestion.github_models import GitHubIssue
from issue_to_patch.patching.models import ValidationReport
from issue_to_patch.patching.push import parse_github_owner_repo
from issue_to_patch.persistence.models import Run
from issue_to_patch.run_states import RunState

ui_router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)

_PAGE_STYLE = "<style>" + Path(__file__).with_name("ui.css").read_text() + "</style>"

# Lives in <head>, so it's never touched by swapPage()'s body.innerHTML
# replacement — these functions stay defined and callable across every
# "soft navigation" (decide, start a new run), unlike a per-page <script>
# embedded inside the swapped body, which the browser would never re-run.
_PAGE_SCRIPT = "<script>" + Path(__file__).with_name("ui.js").read_text() + "</script>"


def _page(title: str, body: str, *, extra_head: str = "") -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)} · Issue-to-Patch</title>{_PAGE_STYLE}{_PAGE_SCRIPT}"
        f"{extra_head}</head>"
        f"<body>{_SHELL}<main id='main'><header class='topbar'>"
        "<span>Workspace <span class='separator'>/</span> Investigations</span>"
        "<span class='workspace-label'>ISSUE-TO-PATCH</span></header>"
        f"<div class='content'>{body}</div><footer>From issue to insight. "
        "From insight to a reviewed patch.</footer></main></body></html>"
    )


def _display_state(run: Run) -> str:
    # Run.state only gets updated once persist_run calls finish_run() — a run
    # still paused at the human-review gate has never reached that node, so
    # the column still holds create_run()'s placeholder default. finished_at
    # is set unconditionally by finish_run(), regardless of which terminal
    # state it finishes with, so its absence is the real "still paused"
    # signal — not the misleading placeholder in `state`.
    return "AWAITING_HUMAN_REVIEW" if run.finished_at is None else run.state


_SHELL = Path(__file__).with_name("ui_shell.html").read_text()


def _badge(state: str) -> str:
    tone = (
        "review"
        if state == "AWAITING_HUMAN_REVIEW"
        else ("success" if state == "PATCH_VALIDATED" else "neutral")
    )
    label = state.replace("_", " ").capitalize()
    return f"<span class='badge {tone}' title='{html.escape(state)}'>{html.escape(label)}</span>"


@ui_router.get("/runs", response_class=HTMLResponse)
def list_runs_page(store: StoreDep) -> HTMLResponse:
    runs = store.list_runs()
    review = sum(_display_state(r) == "AWAITING_HUMAN_REVIEW" for r in runs)
    validated = sum(_display_state(r) == "PATCH_VALIDATED" for r in runs)
    rows = "".join(
        f"<tr data-run data-state='{html.escape(_display_state(r))}'>"
        f"<td><a class='run-link' href='/ui/runs/{html.escape(r.run_id)}'>"
        f"{html.escape(r.issue_ref)}</a><small class='run-id'>{html.escape(r.run_id)}</small></td>"
        f"<td>{_badge(_display_state(r))}</td>"
        f"<td><time datetime='{r.created_at.isoformat()}'>"
        f"{r.created_at.strftime('%b %d, %Y')}<small>"
        f"{r.created_at.strftime('%H:%M %Z')}</small></time></td>"
        f"<td class='cost'>${r.cost_usd:.4f}</td>"
        f"<td><a class='open-run' aria-label='Open run {html.escape(r.run_id)}' "
        f"href='/ui/runs/{html.escape(r.run_id)}'>↗</a></td></tr>"
        for r in runs
    )
    body = (
        "<div class='page-heading'><div><p class='eyebrow'>YOUR ENGINEERING WORKSPACE</p>"
        "<h1>Investigations</h1><p>Turn GitHub issues into patches you can trust.</p></div>"
        "<a class='button secondary' href='#new-issue-url'>+ New investigation</a></div>"
        "<div class='stats'>"
        f"<div><span>Total investigations</span><strong>{len(runs)}<i>↗</i></strong></div>"
        f"<div><span>Awaiting review</span><strong>{review}<i class='amber'>◷</i></strong></div>"
        f"<div><span>Validated patches</span><strong>{validated}"
        "<i class='green'>✓</i></strong></div>"
        f"<div><span>Total run cost</span><strong>${sum(r.cost_usd for r in runs):.4f}"
        "<i>$</i></strong></div></div>"
        f"{_START_RUN_FORM}"
        f"{_REPO_BROWSE_FORM}"
        "<section class='panel history'><div class='section-heading'><div><h2>Run history "
        f"<span class='count'>{len(runs)}</span></h2>"
        "<p>Every investigation, in one place.</p></div>"
        "<div class='table-tools'><input id='run-search' type='search' aria-label='Search runs' "
        "placeholder='Search issues or run IDs…' oninput='filterRuns()'>"
        "<select id='run-filter' aria-label='Filter by status' onchange='filterRuns()'>"
        "<option value=''>All statuses</option><option value='AWAITING_HUMAN_REVIEW'>"
        "Awaiting review</option><option value='PATCH_VALIDATED'>Validated</option>"
        "</select></div></div>"
    )
    if runs:
        body += (
            "<div class='table-scroll'><table><thead><tr><th>Issue / Run</th><th>Status</th>"
            "<th>Created</th><th>Cost</th><th><span class='sr-only'>Open</span></th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
            "<p id='no-results' class='empty-search' hidden>No matching investigations. "
            "Try another search or status.</p>"
        )
    else:
        body += (
            "<div class='empty-state'><div class='empty-icon'>⌘</div>"
            "<h3>A clearer path to your next fix</h3><p>Start with a GitHub issue. "
            "Your investigation and proposed patch will appear here.</p>"
            "<a href='#new-issue-url'>Start your first investigation →</a></div>"
            "<p id='no-results' hidden></p>"
        )
    body += "</section>"
    return _page("Investigations", body)


_START_RUN_FORM = Path(__file__).with_name("ui_start.html").read_text()
_REPO_BROWSE_FORM = Path(__file__).with_name("ui_repo_browse.html").read_text()


@ui_router.get("/repos/issues", response_class=HTMLResponse, response_model=None)
def resolve_repo_issues(repo: str) -> HTMLResponse | RedirectResponse:
    """The repo-browse form's submit target: parses the pasted text with the
    same :func:`parse_github_owner_repo` the push feature already uses (no
    second parser), then redirects to the canonical per-repo page. A bad
    URL gets a friendly inline message on a normal 200, not a 400/404 — a
    mistyped box of text reads more like a no-results search than a broken
    link."""
    parsed = parse_github_owner_repo(repo.strip())
    if parsed is None:
        body = (
            "<a class='back-link' href='/ui/runs'>← All investigations</a>"
            "<div class='page-heading'><div><p class='eyebrow'>BROWSE A REPOSITORY</p>"
            "<h1>Could not read that repository URL</h1>"
            f"<p>{html.escape(repo)} doesn't look like a github.com repository URL. "
            "Try something like https://github.com/owner/repo.</p></div></div>"
        )
        return _page("Browse a repository", body)
    owner, name = parsed
    return RedirectResponse(f"/ui/repos/{owner}/{name}/issues", status_code=303)


@ui_router.get("/repos/{owner}/{repo}/issues", response_class=HTMLResponse)
async def list_repo_issues_page(
    owner: str, repo: str, settings: SettingsDep, cursor: str | None = None
) -> HTMLResponse:
    full_name = f"{owner}/{repo}"
    token = settings.github_token.get_secret_value() if settings.github_token else None
    try:
        async with GitHubClient(token=token, base_url=settings.github_api_base) as client:
            issues, next_cursor, previous_cursor = await list_issue_page(client, full_name, cursor)
    except ValueError:
        return _page(
            "Invalid page",
            _repo_issues_error(
                full_name, "Invalid page cursor. Return to the repository to start again."
            ),
        )
    except GitHubNotFound:
        return _page(
            f"{full_name} issues",
            _repo_issues_error(full_name, "No such repository, or it's private."),
        )
    except GitHubAPIError as exc:
        return _page(f"{full_name} issues", _repo_issues_error(full_name, str(exc)))

    rows = "".join(_issue_row(issue) for issue in issues)
    body = (
        "<a class='back-link' href='/ui/runs'>← All investigations</a>"
        "<div class='page-heading'><div><p class='eyebrow'>BROWSE A REPOSITORY</p>"
        f"<h1>{html.escape(full_name)}</h1>"
        "<p>Open issues, most recently updated first. Pick one to investigate.</p></div></div>"
        "<section class='panel history'><div class='section-heading'><div>"
        f"<h2>Open issues <span class='count'>{len(issues)} on this page</span></h2></div>"
        "<details class='plan-access'><summary>Workspace access</summary>"
        "<label for='api-key'>Workspace API key</label>"
        "<input id='api-key' type='password' autocomplete='off' "
        "onchange='resetPlanAccess()' placeholder='Optional for local use'>"
        "</details></div>"
        "<p id='repo-issues-status' class='feedback' role='status' aria-live='polite'></p>"
    )
    if issues:
        body += (
            "<div class='table-scroll'><table class='issue-table'><thead><tr>"
            "<th>Issue</th><th>Labels</th>"
            "<th>Updated</th><th><span class='sr-only'>Run</span></th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
        )
    else:
        body += (
            "<div class='empty-state'><div class='empty-icon'>⌘</div>"
            "<h3>No open issues on this page</h3><p>Use Next if more pages are available.</p></div>"
        )
    body += "<nav class='issue-pagination' aria-label='Issue pages'>"
    for label, page_cursor in (("Previous", previous_cursor), ("Next", next_cursor)):
        if page_cursor:
            query = html.escape(urlencode({"cursor": page_cursor}))
            body += f"<a class='button secondary' href='?{query}'>{label}</a>"
    body += "</nav>"
    body += "</section>"
    return _page(f"{full_name} issues", body)


def _repo_issues_error(full_name: str, detail: str) -> str:
    return (
        "<a class='back-link' href='/ui/runs'>← All investigations</a>"
        "<div class='page-heading'><div><p class='eyebrow'>BROWSE A REPOSITORY</p>"
        f"<h1>Could not list issues for {html.escape(full_name)}</h1>"
        f"<p>{html.escape(detail)}</p></div></div>"
    )


def _issue_row(issue: GitHubIssue) -> str:
    labels = "".join(
        f"<span class='badge neutral'>{html.escape(label)}</span>" for label in issue.labels
    )
    updated = issue.updated_at.strftime("%b %d, %Y") if issue.updated_at else ""
    return (
        "<tr><td><a class='run-link' href='" + html.escape(issue.html_url) + "' "
        "target='_blank' rel='noopener noreferrer'>"
        f"#{issue.number} {html.escape(issue.title)}</a>"
        f"<small class='run-id'>by {html.escape(issue.author)}</small></td>"
        f"<td>{labels}</td><td><time>{html.escape(updated)}</time></td>"
        "<td><div class='issue-actions'><button class='button secondary plan-issue-btn' "
        f"data-issue-url='{html.escape(issue.html_url)}' aria-expanded='false' "
        f"aria-controls='plan-{issue.number}'>Show resolution plan</button>"
        "<span class='badge neutral plan-indicator' role='status'>Checking saved plans…</span>"
        "<button class='button secondary run-issue-btn' "
        f"data-issue-url='{html.escape(issue.html_url)}'>Run →</button></div></td></tr>"
        f"<tr id='plan-{issue.number}' class='plan-row' hidden><td colspan='4'>"
        "<section class='plan-preview' aria-label='Resolution plan'></section></td></tr>"
    )


@ui_router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail_page(run_id: str, deps: GraphDepsDep, checkpointer: CheckpointerDep) -> HTMLResponse:
    try:
        handle = load_investigation(run_id, deps, checkpointer=checkpointer)
    except UnknownRun as exc:
        pending = pending_runs.get(run_id)
        if pending is None:
            raise HTTPException(404, f"no such run: {run_id}") from exc
        return _pending_run_page(run_id, pending)
    state = handle.state

    sections = [
        "<a class='back-link' href='/ui/runs'>← All investigations</a>"
        "<div class='page-heading'><div><p class='eyebrow'>INVESTIGATION DETAILS</p>"
        f"<h1>Review investigation</h1><p class='run-id'>{html.escape(run_id)}</p></div></div>"
        "<div class='detail-panel'>"
    ]
    if (issue := state.get("issue")) is not None:
        sections.append(
            f"<div class='issue-reference'><span>ISSUE</span>{html.escape(issue.reference)}</div>"
        )

    if hypotheses := state.get("hypotheses"):
        top = hypotheses[0]
        cites = "".join(
            f"<li>{html.escape(loc.path)}:{loc.line_start}-{loc.line_end}"
            f" <code>{html.escape(loc.chunk_id)}</code></li>"
            for loc in top.cites
        )
        sections.append(
            "<section class='review-section'><h2>Diagnosis</h2>"
            f"<p>{html.escape(top.summary)} "
            f"<i>(confidence={top.confidence:.2f})</i></p>"
            f"<ul>{cites}</ul></section>"
        )

    if (patch := state.get("candidate_patch")) is not None:
        sections.append("<section class='review-section'><h2>Code changes</h2>")
        sections.append(render_diff_html(patch.patch_text))
        sections.append("</section>")

    validation: ValidationReport | None = state.get("validation")
    if validation is not None:
        checks = "".join(
            f"<tr><td>{html.escape(c.status.value)}</td><td>{html.escape(c.name)}</td>"
            f"<td>{html.escape(c.detail)}</td></tr>"
            for c in validation.checks
        )
        sections.append(
            "<section class='review-section'><h2>Validation</h2><div class='table-scroll'>"
            f"<table><tr><th>Status</th><th>Check</th><th>Detail</th></tr>{checks}</table>"
            "</div></section>"
        )

    if handle.awaiting_human:
        sections.append(
            "<section class='review-section review-form'>" + _decision_form() + "</section>"
        )
    else:
        final_state = state.get("final_state")
        status = final_state.value if final_state is not None else "IN_PROGRESS"
        sections.append(
            f"<section class='review-section outcome'><h2>Outcome</h2>{_badge(status)}</section>"
        )
        if final_state is RunState.PATCH_VALIDATED:
            snapshot = state.get("repository")
            branch_dir = (
                branch_target(
                    run_id, snapshot, shared_snapshot=bool(state.get("provisional_plan"))
                )[0]
                if snapshot is not None
                else None
            )
            sections.append(_token_section())
            sections.append(_build_push_section(branch_dir))
            if branch_dir is not None and branch_dir.exists():
                sections.append(_pull_request_section(state))

    sections.append("</div>")
    return _page(f"Run {run_id}", "\n".join(sections))


def _token_section() -> str:
    return """
<section class="delivery-card" aria-labelledby="auth-heading">
<div class="delivery-heading"><div>
<p class="eyebrow">AUTHENTICATION</p><h2 id="auth-heading">GitHub personal access token</h2>
<p>Used below to push your branch and to open the pull request — one token, both steps.</p>
</div></div>
<div class="delivery-body">
<label for="github-token">GitHub personal access token</label>
<input id="github-token" type="password" autocomplete="off"
placeholder="Enter your GitHub token" aria-describedby="github-token-help">
<small id="github-token-help">Needs 'repo' scope. No account on this server? This is all you
need — leave blank only if you already have git credentials configured here yourself.
Remembered in this browser only.</small>
</div></section>
"""


def _build_push_section(branch_dir: Path | None) -> str:
    if branch_dir is not None and branch_dir.exists():
        return f"""
<section class="delivery-card" aria-labelledby="push-heading">
<div class="delivery-heading"><span class="step-number">01</span><div>
<p class="eyebrow">PUBLISH YOUR CHANGES</p><h2 id="push-heading">Push to your fork</h2>
<p>Send the reviewed branch to your GitHub repository.</p></div>
<span class="badge success">Local branch ready</span></div>
<div class="delivery-body">
<label for="push-remote-url">Fork repository</label>
<div class="publish-row"><input id="push-remote-url"
aria-describedby="remote-help" placeholder="git@github.com:you/your-fork.git">
<button id="push-btn" onclick="pushRun()">Push branch ↗</button></div>
<small id="remote-help">Use a fork you own. This destination is remembered in your browser.
Uses the GitHub token entered above, if any.</small>
<details class="delivery-details"><summary>Local branch &amp; connection details</summary>
<p>Your branch is ready to inspect or test at:</p>
<code class="branch-location">{html.escape(str(branch_dir))}</code>
<p>Leave the repository field blank to use the server's configured push destination.</p></details>
<p id="push-status" class="delivery-status" role="status" aria-live="polite"></p>
</div></section>
"""
    return """
<section class="delivery-card" aria-labelledby="build-heading">
<div class="delivery-heading"><span class="step-number">01</span><div>
<p class="eyebrow">PREPARE YOUR CHANGES</p><h2 id="build-heading">Build a local branch</h2>
<p>Apply the reviewed patch to a branch you can inspect and test.</p></div></div>
<div class="delivery-body delivery-actions">
<span>Next: push to your fork, then open a pull request.</span>
<button id="build-btn" onclick="buildRun()">Build branch →</button>
<p id="build-status" class="delivery-status" role="status" aria-live="polite"></p>
</div></section>
"""


_STAGE_LABELS = {
    "ingesting": "Fetching the issue and repository…",
    "indexing": "Indexing the codebase…",
    "investigating": "Running the investigation…",
}


def _pending_run_page(run_id: str, pending: pending_runs.PendingAutoRun) -> HTMLResponse:
    heading = (
        "<a class='back-link' href='/ui/runs'>← All investigations</a>"
        "<div class='page-heading'><div><p class='eyebrow'>INVESTIGATION DETAILS</p>"
    )
    if pending.stage == "failed":
        body = (
            heading + "<h1>Investigation failed</h1>"
            f"<p class='run-id'>{html.escape(run_id)}</p></div></div>"
            "<div class='detail-panel'><h2>Could not complete this investigation</h2>"
            f"<p>{html.escape(pending.error or 'Unknown error')}</p></div>"
        )
        return _page(f"Run {run_id}", body)
    label = _STAGE_LABELS.get(pending.stage, "Working…")
    body = (
        heading + f"<h1>{html.escape(label)}</h1>"
        f"<p class='run-id'>{html.escape(run_id)}</p></div></div>"
        "<div class='detail-panel'><p>This page updates automatically every few seconds — "
        "no need to refresh it yourself.</p></div>"
    )
    return _page(f"Run {run_id}", body, extra_head='<meta http-equiv="refresh" content="3">')


def _pull_request_section(state: InvestigationState) -> str:
    # Pre-filled, never silently auto-submitted: the title/body are just a
    # starting point the human can edit before clicking Create — opening a
    # PR is a real, visible, hard-to-reverse action under their own GitHub
    # identity, unlike build/push which only ever touch local disk or a
    # remote they already own.
    issue_ref = ""
    summary = ""
    if (issue := state.get("issue")) is not None:
        issue_ref = issue.reference
    if hypotheses := state.get("hypotheses"):
        summary = hypotheses[0].summary
    default_title = f"Fix: {summary or issue_ref or 'see description'}"
    default_body = f"Fixes {issue_ref}.\n\n{summary}".strip() if (issue_ref or summary) else ""
    return f"""
<section class="delivery-card" aria-labelledby="pr-heading">
<div class="delivery-heading"><span class="step-number">02</span><div>
<p class="eyebrow">READY FOR REVIEW</p><h2 id="pr-heading">Create a Pull Request</h2>
<p>Propose your changes to the original repository after pushing your branch.</p></div></div>
<div class="delivery-body">
<div class="pr-context"><span>RELATED ISSUE</span>
<strong>{html.escape(issue_ref or "This investigation")}</strong></div>
<label for="pr-title">Pull request title</label>
<input id="pr-title" value="{html.escape(default_title)}" placeholder="Describe the fix">
<div class="editor-label"><label for="pr-body">Description</label>
<span>Markdown supported</span></div>
<textarea id="pr-body" rows="9" spellcheck="false"
aria-describedby="pr-body-help">{html.escape(default_body)}</textarea>
<small id="pr-body-help">Explain what changed and how you verified the fix.</small>
<div class="delivery-footer"><p>Uses the GitHub token entered above. Creates a pull request on
GitHub for others to review.</p>
<button id="create-pr-btn" onclick="createPullRequest()">Create Pull Request ↗</button></div>
<p id="pr-status" class="delivery-status" role="status" aria-live="polite"></p>
</div></section>
"""


def _decision_form() -> str:
    # No run_id parameter needed: decide() (in the page-shell-level
    # _PAGE_SCRIPT, not duplicated per-run) reads it straight out of
    # location.pathname once this page is the one on screen.
    #
    # The reviewer/role fields below are a LEGACY fallback, not a real
    # choice: a plain page load has no way to know who's signed in (no
    # bearer header on navigation), so they're shown by default. Once
    # refreshWhoami() confirms a real per-user key, it hides this block and
    # shows a plain "Signed in as X (role)" line instead — the server
    # ignores these two fields entirely once a real account is presented,
    # so hiding them here just makes that honest.
    return """
<h2>Review</h2>
<p id="whoami-status" role="status" aria-live="polite"></p>
<label for="api-key">Workspace API key (optional for local use)</label>
<input id="api-key" type="password" placeholder="leave blank for local/no-auth use">
<div id="legacy-reviewer-fields">
<label for="reviewer">Reviewer name</label>
<input id="reviewer" value="human">
<label for="role">Role</label>
<select id="role">
  <option value="gatekeeper">gatekeeper</option>
  <option value="auditor">auditor</option>
  <option value="strategist">strategist</option>
</select>
</div>
<label for="reason">Reason (optional)</label>
<input id="reason" placeholder="">
<div style="margin-top:1rem">
  <button id="decide-approve" onclick="decide('approve')">Approve</button>
  <button id="decide-reject" onclick="decide('reject')">Reject</button>
  <button id="decide-revise" onclick="decide('revise')">Revise</button>
</div>
<p id="decide-status" role="status" aria-live="polite"></p>
"""
