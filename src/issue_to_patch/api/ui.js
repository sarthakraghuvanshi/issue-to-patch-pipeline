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

async function startAuto() {
  var issueUrl = document.getElementById('new-issue-url').value.trim();
  var scopeRaw = document.getElementById('new-scope').value.trim();
  var statusEl = document.getElementById('new-run-status');
  var btn = document.getElementById('start-auto-btn');
  if (!document.getElementById('investigation-form').reportValidity()) return;
  var body = {issue_url: issueUrl};
  if (scopeRaw) {
    body.scope = scopeRaw.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
  }
  btn.disabled = true;
  btn.textContent = 'Investigating…';
  statusEl.className = 'feedback working';
  statusEl.textContent = 'Investigation in progress. This usually takes a minute or two. Keep this page open.';
  try {
    var resp = await fetch('/runs/auto', {
      method: 'POST', headers: apiKeyHeader(), body: JSON.stringify(body)
    });
    if (resp.ok) {
      var result = await resp.json();
      await swapPage('/ui/runs/' + result.run_id);
    } else {
      statusEl.textContent = 'failed (' + resp.status + '): ' + await resp.text();
      btn.disabled = false;
      btn.textContent = 'Start investigation →';
      statusEl.className = 'feedback error';
    }
  } catch (e) {
    statusEl.textContent = 'error: ' + e;
    btn.disabled = false;
    btn.textContent = 'Start investigation →';
    statusEl.className = 'feedback error';
  }
}

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
  btn.disabled = true;
  status.textContent = 'Pushing…';
  try {
    var resp = await fetch('/runs/' + runId + '/push', {
      method: 'POST', headers: apiKeyHeader(), body: JSON.stringify({ remote_url: remoteUrl || null })
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
