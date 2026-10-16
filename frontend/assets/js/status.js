/* QueueFlow AI — Live queue status (wired to GET /api/public/tokens/{tracking_id}).
 *
 * Honest limits (backend + privacy, spec sections 13-14):
 * - The public tracking API returns ONLY this customer's token: number,
 *   status, people ahead, wait, return time. Session totals, served counts,
 *   other customers' tokens and the shop profile are NOT exposed, so those
 *   spots show "—" instead of anyone else's data.
 * - Alerts are in-app while this page stays open (no background push in MVP).
 */
const API_BASE = (localStorage.getItem('queueflow_api') ||
  (window.location.protocol.indexOf('http') === 0 ? window.location.origin : 'http://127.0.0.1:8000')
).replace(/\/$/, '');

const POLL_MS = 10000;
let trackingId = new URLSearchParams(window.location.search).get('t') || null;
let pollTimer = null;
let alertedCalled = false;
let fetching = false;

if (!trackingId) {
  try {
    const saved = JSON.parse(localStorage.getItem('queueflow_join') || 'null');
    if (saved && saved.tracking_id) trackingId = saved.tracking_id;
  } catch (_) { /* ignore */ }
}

// Shared premium notifications (assets/js/notify.js): one component for
// every page — variants, auto-dismiss, dedupe, reduced-motion + ARIA live.
// Guarded: if the shared lib hasn't loaded (stale cache), nothing throws.
function popup(msg, kind) {
  if (window.Notify) { Notify.show(msg, { kind: kind || 'info' }); return; }
  try { console.warn('[notify]', kind || 'info', msg); } catch (_) {}
}

function fmtReturnTime(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// Whole minutes, minimum 1 for any positive wait: "5 minutes",
// "2 minutes", "14 minutes". Null (uncomputable) stays "—".
function fmtWholeMinutes(m) {
  if (m == null) return '—';
  if (m <= 0) return '0 minutes';
  const n = Math.max(1, Math.ceil(m));
  return `${n} minute${n === 1 ? '' : 's'}`;
}

function cap(s) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function renderLive(t) {
  document.getElementById('tokenNumber').textContent = `#${t.token_number}`;
  document.getElementById('peopleAhead').textContent = t.people_ahead;
  document.getElementById('waitTime').textContent = fmtWholeMinutes(t.estimated_wait_minutes);
  document.getElementById('overviewWait').textContent = fmtWholeMinutes(t.estimated_wait_minutes);
  document.getElementById('aheadSummary').textContent = t.people_ahead;
  document.getElementById('validTill').textContent = fmtReturnTime(t.recommended_return_at) || 'After called';
  document.getElementById('statusText').textContent = cap(t.status);
  // Owner-configured validity (10/30), not a hardcoded 30 minutes.
  const validityLabel = document.getElementById('validityLabel');
  if (validityLabel && t.validity_minutes) {
    validityLabel.innerHTML =
      `Token Validity<br>(${t.validity_minutes} min after called)`;
  }

  // Session-wide counts are not public (privacy) — mark unavailable, never guess.
  document.getElementById('totalTokens').textContent = '—';
  document.getElementById('currentlyWaiting').textContent = '—';
  document.getElementById('servedToday').textContent = '—';
  document.getElementById('servedSummary').textContent = '—';

  const pill = document.getElementById('statusPill');
  const message = document.getElementById('statusMessage');
  pill.style.color = '#087443';
  pill.style.background = '#dcfce7';
  if (t.status === 'called') {
    pill.style.color = '#92400e';
    pill.style.background = '#fef3c7';
    message.textContent = 'It is your turn! Please go to the service counter now.';
  } else if (t.status === 'served') {
    message.textContent = 'Your queue service is marked as completed.';
  } else if (t.status === 'cancelled') {
    pill.style.color = '#b91c1c';
    pill.style.background = '#fee2e2';
    message.textContent = 'This token was cancelled. Scan the queue QR code again to rejoin.';
  } else if (t.status === 'expired') {
    pill.style.color = '#b91c1c';
    pill.style.background = '#fee2e2';
    message.textContent = 'This token expired 30 minutes after it was called. Scan the QR code again to rejoin.';
  } else if (t.people_ahead === 0) {
    message.textContent = 'You are next in line. Please stay nearby.';
  } else {
    message.textContent = 'Your place in the queue is confirmed.';
  }

  renderProgress(t);
  renderTokenList(t);

  const note = document.querySelector('.demo-note');
  if (note) {
    const ret = fmtReturnTime(t.recommended_return_at);
    note.textContent = t.estimate_note + (ret ? ` Suggested return: around ${ret} (approximate).` : '');
  }
  const live = document.querySelector('.live-indicator');
  if (live) live.innerHTML = '<span class="dot"></span>Live';
  document.getElementById('updatedAt').textContent =
    'Updated ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  if (t.status === 'called' && !alertedCalled) {
    alertedCalled = true;
    popup('Your token was called — please head to the service counter now.', 'warning');
    try { document.title = '🔔 Your turn! — QueueFlow AI'; } catch (_) { /* noop */ }
  } else if (t.status === 'waiting' && t.people_ahead <= 2) {
    popup('Almost there — stay nearby, your turn is close.', 'info');
  }
  if (t.status === 'served') {
    popup('You have been served. Thank you!', 'success');
    stopPolling();
  } else if (t.status === 'cancelled') {
    popup('This token was cancelled. Scan the QR code again to rejoin.', 'error');
    stopPolling();
  } else if (t.status === 'expired') {
    popup('This token expired 30 minutes after it was called. Scan the QR code again to rejoin.', 'error');
    stopPolling();
  }
}

function renderProgress(t) {
  const track = document.getElementById('progressTrack');
  const aheadDots = Math.min(Math.max(t.people_ahead, 0), 5);
  let html = '';
  for (let i = aheadDots; i >= 1; i -= 1) {
    html += `<div class="step"><span class="step-circle"></span><span class="step-name">−${i}</span></div>`;
  }
  const youClass = (t.status === 'served' || t.status === 'expired') ? 'step done' : 'step current';
  const youName = t.status === 'served' ? `#${t.token_number}` : 'You';
  html += `<div class="${youClass}"><span class="step-circle"><svg viewBox="0 0 24 24" fill="none"><path d="m5 12 4 4L19 6" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg></span><span class="step-name">${youName}</span></div>`;
  if (t.status !== 'served' && t.status !== 'cancelled' && t.status !== 'expired') {
    html += '<div class="step"><span class="step-circle"></span><span class="step-name">Next</span></div>';
  }
  track.innerHTML = html;
}

function renderTokenList(t) {
  const list = document.getElementById('tokenList');
  const time = t.estimated_wait_minutes == null ? cap(t.status) : fmtWholeMinutes(t.estimated_wait_minutes);
  list.innerHTML =
    `<div class="token-row mine"><span class="id">#${t.token_number}</span>` +
    `<span class="badge">Your Token</span><span class="time">${time}</span><span class="row-wait"></span></div>` +
    '<div class="token-row"><span class="id">···</span><span class="badge">Private</span>' +
    '<span class="time">Hidden</span><span class="row-wait"></span></div>';
  const note = document.createElement('p');
  note.className = 'demo-note';
  note.style.margin = '8px 4px 0';
  note.textContent = 'Other customers’ tokens are private and never shown here.';
  list.appendChild(note);
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function fetchStatus(manual) {
  if (!trackingId) {
    document.getElementById('statusMessage').textContent =
      'No token to track. Open your join link (or the Join page) to get a token first.';
    if (manual) popup('No token to track yet — join the queue first.', 'error');
    return;
  }
  if (fetching) return;
  fetching = true;
  const btn = document.getElementById('refreshButton');
  btn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/api/public/tokens/${encodeURIComponent(trackingId)}`);
    const data = await res.json().catch(() => null);
    if (res.status === 404) {
      document.getElementById('statusMessage').textContent =
        'Token not found. It may belong to a closed session — scan the QR code again.';
      popup('Token not found — scan the latest QR code.', 'error');
      stopPolling();
      return;
    }
    if (!res.ok) {
      popup('Could not refresh status.', 'error');
      return;
    }
    renderLive(data);
  } catch (err) {
    popup('We couldn’t connect. Check your internet connection and try again.', 'error');
  } finally {
    fetching = false;
    btn.disabled = false;
  }
}

document.getElementById('refreshButton').addEventListener('click', () => fetchStatus(true));
document.getElementById('topRefresh').addEventListener('click', () => fetchStatus(true));

fetchStatus(false).then(() => {
  stopPolling();
  pollTimer = setInterval(() => { fetchStatus(false); }, POLL_MS);
});
