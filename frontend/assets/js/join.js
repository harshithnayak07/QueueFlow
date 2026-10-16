/* QueueFlow AI — Customer join + token tracking (wired to public backend APIs).
 *
 * MVP scope notes (honest limits, same as backend):
 * - Backend stores only customer_contact (phone). The name field is
 *   UI-only (no name column on tokens) and is used for greetings.
 * - "30-minute validity" is display text from the approved design; the
 *   backend has no token-expiry logic (future gap).
 * - Alerts are in-app while this page stays open. Nothing is delivered
 *   when the browser is closed (no background push in MVP).
 */
const joinForm = document.getElementById('joinForm');
const detailsCard = document.getElementById('detailsCard');
const tokenCard = document.getElementById('tokenCard');
const mobileInput = document.getElementById('mobile');
const submitBtn = joinForm.querySelector('.primary');

// Same-origin when served by the backend (http), localhost fallback for file://.
const API_BASE = (localStorage.getItem('queueflow_api') ||
  (window.location.protocol.indexOf('http') === 0 ? window.location.origin : 'http://127.0.0.1:8000')
).replace(/\/$/, '');

const SESSION_ID = Number(new URLSearchParams(window.location.search).get('session') || 0) || null;
const POLL_MS = 10000;
let pollTimer = null;
let lastStatus = null;
let alertedCalled = false;

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

mobileInput.addEventListener('input', () => {
  mobileInput.value = mobileInput.value.replace(/\D/g, '').slice(0, 10);
});

function showTokenCard() {
  detailsCard.classList.add('hidden');
  tokenCard.classList.remove('hidden');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function renderTracking(t, name) {
  document.getElementById('welcomeText').textContent =
    `Thanks${name ? `, ${name}` : ''}. This is your live queue token.`;
  document.getElementById('tokenNumber').textContent = `#${t.token_number}`;
  document.getElementById('peopleAhead').textContent =
    t.people_ahead === 1 ? '1 person' : `${t.people_ahead} people`;
  document.getElementById('waitTime').textContent = fmtWholeMinutes(t.estimated_wait_minutes);
  // Owner-configured validity (10/30), not a hardcoded 30 minutes.
  const validityTitle = document.getElementById('validityTitle');
  if (validityTitle && t.validity_minutes) {
    validityTitle.textContent =
      `Token validity: ${t.validity_minutes} minutes after you're called.`;
  }
  const badge = document.getElementById('statusBadge');
  badge.textContent = t.status.charAt(0).toUpperCase() + t.status.slice(1);
  const back = document.getElementById('backButton');
  back.style.display = (t.status === 'served' || t.status === 'cancelled' || t.status === 'expired') ? 'none' : '';

  // Replace the demo disclaimer with the live estimate note (+ return time).
  const note = document.querySelector('.demo-note');
  if (note) {
    const ret = fmtReturnTime(t.recommended_return_at);
    note.textContent = t.estimate_note + (ret ? ` Suggested return: around ${ret} (approximate).` : '');
  }

  // Approaching-turn alert (in-app, while this page is open).
  if (t.status === 'called' && !alertedCalled) {
    alertedCalled = true;
    popup('Your token was called — please return to the counter now.', 'warning');
    try { document.title = '🔔 Your turn! — QueueFlow AI'; } catch (_) { /* noop */ }
  } else if (t.status === 'waiting' && t.people_ahead <= 2) {
    popup(`Almost there — only ${t.people_ahead === 0 ? 'no one' : t.people_ahead + ' ahead of you'}. Stay nearby.`, 'info');
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
  lastStatus = t.status;
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

async function trackOnce(trackingId, name) {
  let res;
  try {
    res = await fetch(`${API_BASE}/api/public/tokens/${trackingId}`);
  } catch (err) {
    popup('We couldn’t connect. Check your internet connection and try again.', 'error');
    return false;
  }
  const data = await res.json().catch(() => null);
  if (res.status === 404) {
    popup('Token not found. It may belong to a closed session — scan the QR code again.', 'error');
    stopPolling();
    return false;
  }
  if (!res.ok) {
    popup('Could not refresh status.', 'error');
    return false;
  }
  renderTracking(data, name);
  return true;
}

function startPolling(trackingId, name) {
  stopPolling();
  pollTimer = setInterval(() => { trackOnce(trackingId, name); }, POLL_MS);
}

joinForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!joinForm.reportValidity()) return;

  const name = document.getElementById('customerName').value.trim();
  if (!name) {
    document.getElementById('customerName').focus();
    return;
  }
  const mobile = mobileInput.value.trim();
  if (!/^[6-9][0-9]{9}$/.test(mobile)) {
    popup('Enter a valid 10-digit Indian mobile number.', 'warning');
    mobileInput.focus();
    return;
  }
  if (!SESSION_ID) {
    popup('This join link has no queue session. Please scan the latest QR code from the counter.', 'error');
    return;
  }

  submitBtn.disabled = true;
  const originalLabel = submitBtn.innerHTML;
  submitBtn.textContent = 'Getting token...';
  try {
    // NOTE: backend stores contact only; name is UI-only (no token name column).
    const res = await fetch(`${API_BASE}/api/public/queue-sessions/${SESSION_ID}/tokens`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ customer_contact: `+91${mobile}` }),
    });
    const data = await res.json().catch(() => null);
    if (res.status === 404) {
      popup('Queue session not found. Ask the counter for a fresh QR code.', 'error');
      return;
    }
    if (res.status === 403) {
      // Backend distinguishes inactive / unconfirmed / full with curated
      // messages ("Queue is full for this session..." etc.) — show it.
      const msg = (data && typeof data.detail === 'string')
        ? data.detail
        : 'This queue is not accepting new customers right now.';
      popup(msg, 'error');
      return;
    }
    if (res.status === 409) {
      // Backend distinguishes closed session vs duplicate number with
      // curated messages ("already has token #N..." etc.) — show it.
      const msg = (data && typeof data.detail === 'string')
        ? data.detail
        : 'This queue session is closed. Ask the counter for help.';
      popup(msg, 'error');
      return;
    }
    if (!res.ok) {
      popup('Could not get a token. Try again.', 'error');
      return;
    }
    try {
      localStorage.setItem('queueflow_join', JSON.stringify({
        session_id: SESSION_ID, tracking_id: data.tracking_id,
        token_number: data.token_number, name,
      }));
    } catch (_) { /* private mode: tracking still works for this visit */ }
    alertedCalled = false;
    renderTracking(data, name);
    showTokenCard();
    popup(`You’ve joined the queue successfully. Token #${data.token_number} — keep this page open for turn alerts.`, 'success');
    startPolling(data.tracking_id, name);
  } catch (err) {
    popup('We couldn’t connect. Check your internet connection and try again.', 'error');
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = originalLabel;
  }
});

document.getElementById('backButton').addEventListener('click', () => {
  stopPolling();
  tokenCard.classList.add('hidden');
  detailsCard.classList.remove('hidden');
  window.scrollTo({ top: 0, behavior: 'smooth' });
});

document.getElementById('trackButton').addEventListener('click', () => {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem('queueflow_join') || 'null'); } catch (_) { /* ignore */ }
  if (!saved || !saved.tracking_id) {
    popup('Get a token first — then you can open live tracking.', 'error');
    return;
  }
  window.location.href = `status.html?t=${encodeURIComponent(saved.tracking_id)}`;
});

// Revisit: restore a stored token for this session and resume live tracking.
(function restore() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem('queueflow_join') || 'null'); } catch (_) { /* ignore */ }
  if (!saved || !saved.tracking_id) return;
  if (SESSION_ID && saved.session_id !== SESSION_ID) return;
  const name = saved.name || '';
  if (name) {
    try { document.getElementById('customerName').value = name; } catch (_) { /* noop */ }
  }
  showTokenCard();
  trackOnce(saved.tracking_id, name).then((ok) => {
    if (ok) startPolling(saved.tracking_id, name);
  });
})();
