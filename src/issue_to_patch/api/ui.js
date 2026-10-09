function prefillSavedFields() {
  var el = document.getElementById('api-key');
  if (el) {
    var saved = localStorage.getItem('itp_api_key');
    if (saved) el.value = saved;
  }
  var remoteEl = document.getElementById('push-remote-url');
  if (remoteEl) {
    var savedRemote = localStorage.getItem('itp_push_remote_url');
    if (savedRemote) remoteEl.value = savedRemote;
  }
  var githubTokenEl = document.getElementById('github-token');
  if (githubTokenEl) {
    var savedGithubToken = localStorage.getItem('itp_github_token');
    if (savedGithubToken) githubTokenEl.value = savedGithubToken;
  }
  refreshWhoami();
  refreshPlanSummaries();
}

async function refreshWhoami() {
  // A plain page load can't know who's signed in (no bearer header on
  // navigation) — this is the only way the browser finds out, by asking
  // with whatever key it has saved.
  var whoamiEl = document.getElementById('whoami-status');
  var legacyFields = document.getElementById('legacy-reviewer-fields');
  if (!whoamiEl) return;  // not on a page with the decision form
  try {
    var resp = await fetch('/users/me', { headers: apiKeyHeader() });
    if (!resp.ok) {
      whoamiEl.textContent = '';
      if (legacyFields) legacyFields.hidden = false;
      return;
    }
    var user = await resp.json();
    if (user.authenticated) {
      whoamiEl.textContent = 'Signed in as ' + user.username + ' (' + user.role + ')';
      if (legacyFields) legacyFields.hidden = true;
    } else {
      whoamiEl.textContent = '';
      if (legacyFields) legacyFields.hidden = false;
    }
  } catch (error) {
    whoamiEl.textContent = '';
    if (legacyFields) legacyFields.hidden = false;
  }
}

function apiKeyHeader() {
  var el = document.getElementById('api-key');
  var key = el ? el.value : (localStorage.getItem('itp_api_key') || '');
  if (key) localStorage.setItem('itp_api_key', key);
  else localStorage.removeItem('itp_api_key');
  var headers = {'Content-Type': 'application/json'};
  if (key) headers['Authorization'] = 'Bearer ' + key;
  return headers;
}

async function swapPage(url, push = true) {
  var resp = await fetch(url);
  if (!resp.ok) throw new Error('Could not load this page. Please refresh.');
  var text = await resp.text();
  var doc = new DOMParser().parseFromString(text, 'text/html');
  stopPlanPolling();
  document.body.innerHTML = doc.body.innerHTML;
  document.title = doc.title;
  if (push) history.pushState({}, '', url);
  prefillSavedFields();
}

async function decide(action) {
  var match = location.pathname.match(/\/ui\/runs\/([^/]+)/);
  if (!match) return;
  var runId = match[1];
  var body = {
    decision: action,
    reviewer: document.getElementById('reviewer').value || 'human',
    role: document.getElementById('role').value,
    reason: document.getElementById('reason').value
  };
  var status = document.getElementById('decide-status');
  var buttons = document.querySelectorAll('button[id^="decide-"]');
  buttons.forEach(function (button) { button.disabled = true; });
  status.textContent = 'Submitting your decision…';
  try {
    var resp = await fetch('/runs/' + runId + '/approve', {
      method: 'POST', headers: apiKeyHeader(), body: JSON.stringify(body)
    });
    if (resp.ok) {
      await swapPage(location.pathname, false);
    } else {
      status.textContent = 'Could not submit (' + resp.status + '): ' + await resp.text();
    }
  } catch (error) {
    status.textContent = 'Connection interrupted. Refresh to check the decision before retrying.';
  } finally {
    buttons.forEach(function (button) { button.disabled = false; });
  }
}

async function runAutoInvestigation(issueUrl, scope, statusEl, btn, busyLabel, idleLabel, planId) {
  btn.disabled = true;
  btn.textContent = busyLabel;
  statusEl.className = 'feedback working';
  statusEl.textContent = 'Investigation in progress. This usually takes a minute or two. Keep this page open.';
  var body = {issue_url: issueUrl};
  if (planId) body.plan_id = planId;
  if (scope && scope.length) body.scope = scope;
  try {
    var resp = await fetch('/runs/auto', {
      method: 'POST', headers: apiKeyHeader(), body: JSON.stringify(body)
    });
    if (resp.ok) {
      var result = await resp.json();
      await swapPage('/ui/runs/' + result.run_id);
    } else {
      statusEl.textContent = 'failed (' + resp.status + '): ' + await resp.text();
      statusEl.className = 'feedback error';
      btn.disabled = false;
      btn.textContent = idleLabel;
    }
  } catch (e) {
    statusEl.textContent = 'error: ' + e;
    statusEl.className = 'feedback error';
    btn.disabled = false;
    btn.textContent = idleLabel;
  }
}

async function startAuto() {
  var issueUrl = document.getElementById('new-issue-url').value.trim();
  var scopeRaw = document.getElementById('new-scope').value.trim();
  if (!document.getElementById('investigation-form').reportValidity()) return;
  var scope = scopeRaw ? scopeRaw.split(',').map(function (s) { return s.trim(); }).filter(Boolean) : [];
  await runAutoInvestigation(
    issueUrl, scope,
    document.getElementById('new-run-status'), document.getElementById('start-auto-btn'),
    'Investigating…', 'Start investigation →'
  );
}

// Delegated (not bound per-row): the issue list is rendered server-side and
// re-fetched on every navigation, so a direct listener would need to be
// re-attached each time — this one, registered once here in <head>, keeps
// working across every swapPage() without that bookkeeping.
document.addEventListener('click', function (e) {
  var btn = e.target.closest('.run-issue-btn');
  if (!btn) return;
  var statusEl = document.getElementById('repo-issues-status');
  if (!statusEl) return;
  runAutoInvestigation(btn.dataset.issueUrl, [], statusEl, btn, 'Starting…', btn.textContent);
});

async function buildRun() {
  var match = location.pathname.match(/\/ui\/runs\/([^/]+)/);
  if (!match) return;
  var runId = match[1];
  var btn = document.getElementById('build-btn');
  var status = document.getElementById('build-status');
  btn.disabled = true;
  status.textContent = 'Building a persistent branch…';
  try {
    var resp = await fetch('/runs/' + runId + '/build', { method: 'POST', headers: apiKeyHeader() });
    if (resp.ok) {
      await swapPage(location.pathname, false);
    } else {
      status.textContent = 'Could not build (' + resp.status + '): ' + await resp.text();
      btn.disabled = false;
    }
  } catch (error) {
    status.textContent = 'Connection interrupted. Refresh to check before retrying.';
    btn.disabled = false;
  }
}

async function pushRun() {
  var match = location.pathname.match(/\/ui\/runs\/([^/]+)/);
  if (!match) return;
  var runId = match[1];
  var btn = document.getElementById('push-btn');
  var status = document.getElementById('push-status');
  var remoteEl = document.getElementById('push-remote-url');
  var remoteUrl = remoteEl ? remoteEl.value.trim() : '';
  if (remoteUrl) localStorage.setItem('itp_push_remote_url', remoteUrl);
  else localStorage.removeItem('itp_push_remote_url');
  // Same GitHub token as "Create a Pull Request" below — reused here so a
  // visitor with no git credentials on this server can still push, using
  // only their own token over https://. Harmless to send for an ssh/scp
  // remote too: the server ignores it when the URL has no https:// scheme.
  var tokenEl = document.getElementById('github-token');
  var pushToken = tokenEl ? tokenEl.value.trim() : '';
  btn.disabled = true;
  status.textContent = 'Pushing…';
  try {
    var resp = await fetch('/runs/' + runId + '/push', {
      method: 'POST', headers: apiKeyHeader(),
      body: JSON.stringify({ remote_url: remoteUrl || null, token: pushToken || null })
    });
    var body = await resp.json().catch(function () { return null; });
    if (resp.ok && body && body.ok) {
      status.textContent = 'Pushed ' + body.detail + ' to ' + body.remote_display;
    } else {
      status.textContent = 'Could not push: ' + (body ? (body.detail || JSON.stringify(body)) : await resp.text());
    }
  } catch (error) {
    status.textContent = 'Connection interrupted. Refresh to check before retrying.';
  } finally {
    btn.disabled = false;
  }
}

async function createPullRequest() {
  var match = location.pathname.match(/\/ui\/runs\/([^/]+)/);
  if (!match) return;
  var runId = match[1];
  var btn = document.getElementById('create-pr-btn');
  var status = document.getElementById('pr-status');
  var remoteEl = document.getElementById('push-remote-url');
  var remoteUrl = remoteEl ? remoteEl.value.trim() : '';
  var tokenEl = document.getElementById('github-token');
  var token = tokenEl ? tokenEl.value.trim() : '';
  if (token) localStorage.setItem('itp_github_token', token);
  else localStorage.removeItem('itp_github_token');
  if (!remoteUrl) {
    status.textContent = 'Enter the fork remote you pushed to, above, first.';
    return;
  }
  btn.disabled = true;
  status.textContent = 'Creating pull request…';
  try {
    var resp = await fetch('/runs/' + runId + '/pull-request', {
      method: 'POST',
      headers: apiKeyHeader(),
      body: JSON.stringify({
        fork_remote_url: remoteUrl,
        title: document.getElementById('pr-title').value,
        body: document.getElementById('pr-body').value,
        github_token: token || null
      })
    });
    var body = await resp.json().catch(function () { return null; });
    if (resp.ok && body) {
      status.textContent = 'Pull request created: ';
      var link = document.createElement('a');
      link.href = body.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = body.url;
      status.appendChild(link);
    } else {
      status.textContent = 'Could not create pull request: ' +
        (body ? (body.detail || JSON.stringify(body)) : await resp.text());
      btn.disabled = false;
    }
  } catch (error) {
    status.textContent = 'Connection interrupted. Refresh to check before retrying.';
    btn.disabled = false;
  }
}

function filterRuns() {
  var query = document.getElementById('run-search').value.toLowerCase();
  var state = document.getElementById('run-filter').value;
  var visible = 0;
  document.querySelectorAll('[data-run]').forEach(function (row) {
    row.hidden = !row.textContent.toLowerCase().includes(query) ||
      (state && row.dataset.state !== state);
    if (!row.hidden) visible++;
  });
  document.getElementById('no-results').hidden = visible !== 0;
}
window.addEventListener('DOMContentLoaded', prefillSavedFields);
window.addEventListener('popstate', function () { swapPage(location.pathname, false).catch(function () { location.reload(); }); });

// Each preview owns its request state, so one issue never replaces another's plan.
var planJobs = new Map();
var planLookupController;
var planLookupTimer;
var planStages = {
  fetching: 'Fetching issue', preparing: 'Preparing repository',
  retrieving: 'Finding relevant code', writing: 'Writing plan'
};
function stopPlanPolling() {
  clearTimeout(planLookupTimer);
  if (planLookupController) planLookupController.abort();
  planJobs.forEach(function (job) {
    clearTimeout(job.timer);
    if (job.controller) job.controller.abort();
  });
  planJobs.clear();
}
window.addEventListener('pagehide', stopPlanPolling);
function planElement(tag, text, className) {
  var el = document.createElement(tag);
  if (text) el.textContent = text;
  if (className) el.className = className;
  return el;
}
function planButton(text, action) {
  var button = planElement('button', text, 'button secondary');
  button.type = 'button';
  button.addEventListener('click', action);
  return button;
}
async function planFetch(job, url, options) {
  job.controller = new AbortController();
  var response = await fetch(url, Object.assign({}, options || {}, {
    headers: apiKeyHeader(), signal: job.controller.signal
  }));
  var data = await response.json().catch(function () { return {}; });
  if (!response.ok) {
    var error = new Error(typeof data.detail === 'string' ? data.detail :
      'Request failed (' + response.status + '). Check your access key and try again.');
    error.status = response.status;
    throw error;
  }
  return data;
}
function planError(job, message) {
  if (!job.panel.isConnected) return;
  job.status.textContent = message;
  job.status.className = 'feedback error';
  job.busy = false;
  renderPlanActions(job);
}
function renderPlanActions(job) {
  job.actions.replaceChildren();
  if (!job.busy) {
    job.actions.appendChild(planButton(job.data && job.data.status === 'ready' ? 'Regenerate' : 'Retry',
      function () { requestPlan(job, true); }));
  }
  if (job.data && job.data.result && job.data.status === 'ready' && !job.busy) {
    var start = planButton('Start investigation →', function () {
      runAutoInvestigation(job.url, [], job.status, start,
        'Starting…', 'Start investigation →', job.id);
    });
    job.actions.appendChild(start);
  }
  job.actions.appendChild(planButton('Collapse', function () {
    job.row.hidden = true;
    job.trigger.setAttribute('aria-expanded', 'false');
    updatePlanIndicator(job.trigger, job.data);
    job.trigger.focus();
  }));
}
function renderPlanResult(job, data) {
  job.content.replaceChildren();
  if (!data.result) return;
  job.content.appendChild(planElement('p',
    'AI starting point · Code reviewed at commit ' + (data.commit_sha || '').slice(0, 12), 'eyebrow'));
  if (data.context_truncated) job.content.appendChild(planElement('p',
    'Issue or discussion context was shortened to fit the planning budget.', 'plan-note'));
  job.content.appendChild(planElement('h3', 'Understanding'));
  job.content.appendChild(planElement('p', data.result.understanding));
  var references = new Map(data.sources.map(function (source) { return [source.chunk_id, source]; }));
  function referenceLink(id) {
    var source = references.get(id);
    if (!source) return planElement('span', 'Unavailable reference');
    var link = planElement('a', source.path + ':' + source.line_start + '–' + source.line_end);
    link.href = '#';
    link.addEventListener('click', async function (event) {
      event.preventDefault();
      var excerpt = job.content.querySelector('.plan-source');
      if (!excerpt) {
        excerpt = planElement('pre', '', 'plan-source');
        excerpt.tabIndex = 0;
        job.content.appendChild(excerpt);
      }
      excerpt.textContent = 'Loading saved source…';
      try {
        // Independent request: inspecting source must not cancel status polling.
        var response = await fetch('/issue-plans/' + job.id + '/source?chunk_id=' +
          encodeURIComponent(id), {headers: apiKeyHeader()});
        if (!response.ok) throw new Error('Could not load the saved source.');
        var detail = await response.json();
        excerpt.textContent = detail.path + ':' + detail.line_start + '–' + detail.line_end +
          '\n\n' + detail.content;
      } catch (error) { excerpt.textContent = error.message; }
      if (excerpt.isConnected) excerpt.focus();
    });
    return link;
  }
  job.content.appendChild(planElement('h3', 'Suggested approach'));
  var steps = planElement('ol');
  data.result.steps.forEach(function (step) {
    var item = planElement('li', step.text);
    step.references.forEach(function (id) { item.append(' ', referenceLink(id)); });
    steps.appendChild(item);
  });
  job.content.appendChild(steps);
  job.content.appendChild(planElement('h3', 'Relevant code'));
  var sources = planElement('ul');
  data.sources.forEach(function (source) {
    var item = planElement('li'); item.appendChild(referenceLink(source.chunk_id)); sources.appendChild(item);
  });
  if (!data.sources.length) sources.appendChild(planElement('li', 'No relevant code evidence found.'));
  job.content.appendChild(sources);
  [['How to verify', data.result.verification], ['Open questions', data.result.open_questions]].forEach(function (group) {
    job.content.appendChild(planElement('h3', group[0]));
    var list = planElement('ul');
    group[1].forEach(function (text) { list.appendChild(planElement('li', text)); });
    if (!group[1].length) list.appendChild(planElement('li', 'None identified in this preliminary plan.'));
    job.content.appendChild(list);
  });
  job.content.appendChild(planElement('p', 'Suggested checks have not been executed.', 'plan-note'));
}
async function pollPlan(job) {
  if (!job.panel.isConnected) return;
  try {
    var data = await planFetch(job, '/issue-plans/' + job.id);
    if (!job.panel.isConnected) return;
    job.data = data;
    updatePlanIndicator(job.trigger, data);
    job.busy = Boolean(planStages[data.status]);
    job.status.textContent = planStages[data.status] ? planStages[data.status] + '…' :
      data.error || 'Plan ready for brainstorming.';
    job.status.className = 'feedback' + (data.error ? ' error' : job.busy ? ' working' : '');
    renderPlanResult(job, data);
    renderPlanActions(job);
    if (job.busy) job.timer = setTimeout(function () { pollPlan(job); }, 2000);
  } catch (error) {
    if (error.status === 429 && job.panel.isConnected) {
      job.status.textContent = 'Waiting for the request limit to reset…';
      job.timer = setTimeout(function () { pollPlan(job); }, 10000);
    } else if (error.name !== 'AbortError') {
      if (error.status === 404) {
        job.id = null;
        job.data = null;
        updatePlanIndicator(job.trigger, null);
      }
      planError(job, error.message);
    }
  }
}
async function requestPlan(job, regenerate) {
  if (job.busy) return;
  clearTimeout(job.timer);
  job.busy = true;
  job.status.textContent = 'Requesting resolution plan…';
  job.status.className = 'feedback working';
  renderPlanActions(job);
  try {
    var url = regenerate && job.id ? '/issue-plans/' + job.id + '/regenerate' : '/issue-plans';
    var data = await planFetch(job, url, {method: 'POST', body: JSON.stringify({
      issue_url: job.url, request_id: job.requestId
    })});
    job.id = data.plan_id;
    await pollPlan(job);
  } catch (error) { if (error.name !== 'AbortError') planError(job, error.message); }
}
document.addEventListener('click', function (event) {
  var trigger = event.target.closest('.plan-issue-btn');
  if (!trigger) return;
  var row = document.getElementById(trigger.getAttribute('aria-controls'));
  var panel = row.querySelector('.plan-preview');
  var job = planJobs.get(row.id);
  row.hidden = false;
  trigger.setAttribute('aria-expanded', 'true');

  if (!job) {
    var status = planElement('p', '', 'feedback');
    status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    job = {row: row, panel: panel, trigger: trigger, url: trigger.dataset.issueUrl,
      requestId: Array.from(crypto.getRandomValues(new Uint8Array(16)), function (n) {
        return n.toString(16).padStart(2, '0');
      }).join(''), status: status, content: planElement('div'),
      actions: planElement('div', '', 'plan-actions'), busy: false};
    panel.append(job.status, job.content, job.actions);
    planJobs.set(row.id, job);
    if (trigger.dataset.planId) {
      job.id = trigger.dataset.planId;
      job.busy = true;
      job.status.textContent = 'Loading saved plan…';
      pollPlan(job);
    } else requestPlan(job, false);
  }
});


function updatePlanIndicator(trigger, data) {
  var indicator = trigger.parentElement.querySelector('.plan-indicator');
  var active = data && Boolean(planStages[data.status]);
  var hasResult = data && (data.has_result || data.result);
  trigger.dataset.planId = data ? data.plan_id : '';
  trigger.textContent = active ? 'View progress' : hasResult ? 'View plan' :
    data ? 'View plan status' : 'Show resolution plan';
  if (indicator) {
    indicator.textContent = active ? (hasResult ? 'Updating plan' : 'Plan in progress') :
      hasResult ? (data.status === 'ready' ? 'Plan ready' : 'Saved plan · retry failed update') :
      data ? (data.status === 'interrupted' ? 'Plan interrupted' : 'Plan failed') : 'No plan yet';
    indicator.className = 'badge plan-indicator ' + (hasResult && !active ? 'success' : 'neutral');
  }
}
async function refreshPlanSummaries() {
  clearTimeout(planLookupTimer);
  if (planLookupController) planLookupController.abort();
  var triggers = Array.from(document.querySelectorAll('.plan-issue-btn'));
  if (!triggers.length) return;
  var controller = new AbortController();
  planLookupController = controller;
  var query = new URLSearchParams();
  triggers.forEach(function (trigger) { query.append('issue_url', trigger.dataset.issueUrl); });
  try {
    var response = await fetch('/issue-plans?' + query.toString(), {
      headers: apiKeyHeader(), signal: controller.signal
    });
    if (!response.ok) throw new Error('Could not check saved plans');
    var summaries = await response.json();
    if (controller.signal.aborted) return;
    var byIssue = new Map(summaries.map(function (data) { return [data.issue_url.toLowerCase(), data]; }));
    var pending = false;
    triggers.forEach(function (trigger) {
      if (!trigger.isConnected) return;
      var data = byIssue.get(trigger.dataset.issueUrl.toLowerCase());
      var job = planJobs.get(trigger.getAttribute('aria-controls'));
      if (!job) updatePlanIndicator(trigger, data);
      if (data && planStages[data.status]) pending = true;
    });
    if (pending) planLookupTimer = setTimeout(refreshPlanSummaries, 5000);
  } catch (error) {
    if (error.name === 'AbortError') return;
    triggers.forEach(function (trigger) {
      if (!trigger.isConnected) return;
      var indicator = trigger.parentElement.querySelector('.plan-indicator');
      if (indicator) indicator.textContent = 'Saved plan status unavailable';
    });
    planLookupTimer = setTimeout(refreshPlanSummaries, 10000);
  }
}

function resetPlanAccess() {
  apiKeyHeader();
  stopPlanPolling();
  document.querySelectorAll('.plan-row').forEach(function (row) {
    row.hidden = true;
    row.querySelector('.plan-preview').replaceChildren();
  });
  document.querySelectorAll('.plan-issue-btn').forEach(function (trigger) {
    trigger.setAttribute('aria-expanded', 'false');
    delete trigger.dataset.planId;
  });
  refreshPlanSummaries();
}
