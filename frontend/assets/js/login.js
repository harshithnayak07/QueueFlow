const form = document.getElementById('loginForm');
const message = document.getElementById('message');
const submitBtn = form.querySelector('.submit');
const emailInput = document.getElementById('email');
const passwordInput = document.getElementById('password');
const rememberBox = document.getElementById('remember');

// Same-origin when served by the backend (http), localhost fallback for file://.
// Override via localStorage.setItem('queueflow_api', 'http://host:port') if needed.
const API_BASE = (localStorage.getItem('queueflow_api') ||
  (window.location.protocol.indexOf('http') === 0 ? window.location.origin : 'http://127.0.0.1:8000')
).replace(/\/$/, '');

// Must match backend settings.google_client_id (public identifier).
const GOOGLE_CLIENT_ID = '650223765036-q2s9vdbg607mett9ld348plp46s36p54.apps.googleusercontent.com';

function showMessage(msg, isError) {
  message.textContent = msg;
  message.classList.toggle('show', true);
  message.classList.toggle('error', Boolean(isError));
  popup(msg, isError ? 'error' : 'success');
}

// Popup toast (matches signup page + dashboard style). Inline message stays too.
// Shared premium notifications (assets/js/notify.js): one component for
// every page — variants, auto-dismiss, dedupe, reduced-motion + ARIA live.
// Guarded: if the shared lib hasn't loaded (stale cache), the inline
// message above still shows and nothing throws.
function popup(msg, kind) {
  if (window.Notify) { Notify.show(msg, { kind: kind || 'info' }); return; }
  try { console.warn('[notify]', kind || 'info', msg); } catch (_) {}
}

// Prefill email left by the signup page; remember-me restores the last email.
const savedEmail = localStorage.getItem('queueflow_email');
if (savedEmail && !emailInput.value) emailInput.value = savedEmail;

document.getElementById('togglePassword').addEventListener('click', () => {
  passwordInput.type = passwordInput.type === 'password' ? 'text' : 'password';
  document.getElementById('togglePassword').setAttribute(
    'aria-label', passwordInput.type === 'password' ? 'Show password' : 'Hide password'
  );
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  message.classList.remove('show', 'error');

  const email = emailInput.value.trim();
  const password = passwordInput.value;
  const emailBad = !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  const passwordBad = !password;
  document.getElementById('emailField').classList.toggle('invalid', emailBad);
  document.getElementById('passwordField').classList.toggle('invalid', passwordBad);
  if (emailBad || passwordBad) {
    (emailBad ? emailInput : passwordInput).focus();
    return;
  }

  submitBtn.disabled = true;
  const originalLabel = submitBtn.innerHTML;
  submitBtn.textContent = 'Logging in...';
  try {
    const loginRes = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const loginData = await loginRes.json().catch(() => null);

    if (loginRes.status === 401) {
      document.getElementById('emailField').classList.add('invalid');
      document.getElementById('passwordField').classList.add('invalid');
      showMessage('Email or password is incorrect.', true);
      return;
    }
    if (!loginRes.ok) {
      showMessage('Could not log you in. Try again.', true);
      return;
    }

    await completeLogin(loginData.access_token, email);
  } catch (err) {
    showMessage('We couldn’t connect. Check your internet connection and try again.', true);
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = originalLabel;
  }
});

document.getElementById('forgotLink').addEventListener('click', (event) => {
  event.preventDefault();
  showMessage('Password recovery is not implemented yet. Add a secure reset flow when the backend is ready.', true);
});

// Shared finish for email + Google logins: store token (remember-me aware),
// pull the profile for the dashboard header, then enter.
async function completeLogin(accessToken, emailHint) {
  const store = rememberBox.checked ? localStorage : sessionStorage;
  const other = rememberBox.checked ? sessionStorage : localStorage;
  other.removeItem('queueflow_token');
  store.setItem('queueflow_token', accessToken);
  if (emailHint) localStorage.setItem('queueflow_email', emailHint);
  try {
    const meRes = await fetch(`${API_BASE}/api/auth/me`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (meRes.ok) {
      const me = await meRes.json();
      if (me.shop_name) localStorage.setItem('queueflow_shop', me.shop_name);
      if (me.email) localStorage.setItem('queueflow_email', me.email);
    }
  } catch (_) { /* profile is a nice-to-have; login already succeeded */ }
  const shop = localStorage.getItem('queueflow_shop');
  showMessage(`Welcome${shop ? `, ${shop}` : ''}. Signed in — opening your dashboard...`, false);
  setTimeout(() => { window.location.href = 'dashboard.html'; }, 700);
}

function googleReady() {
  try {
    return typeof google !== 'undefined' && !!(google.accounts && google.accounts.id);
  } catch (_) {
    return false;
  }
}

// Initialize Google Identity Services when its script loads (needs internet).
// Uses the official rendered button (FedCM-safe). No One Tap prompt() calls:
// Chrome is removing the prompt status callbacks they depend on.
window.addEventListener('load', () => {
  try {
    if (googleReady()) {
      google.accounts.id.initialize({ client_id: GOOGLE_CLIENT_ID, callback: onGoogleCredential, use_fedcm_for_prompt: true });
      renderGoogleButton();
    } else {
      googleFallback();
    }
  } catch (_) { googleFallback(); }
});

function renderGoogleButton() {
  const slot = document.getElementById('gSignInBtn');
  if (!slot) return;
  try {
    google.accounts.id.renderButton(slot, {
      theme: 'outline', size: 'large', shape: 'rectangular',
      text: 'continue_with', width: Math.min(slot.clientWidth || 300, 400),
    });
  } catch (_) { googleFallback(); }
}

function googleFallback() {
  const slot = document.getElementById('gSignInBtn');
  if (slot && !slot.children.length) {
    slot.innerHTML = '<p class="google-unavailable">Google sign-in needs internet. Email login works without it.</p>';
  }
}

async function onGoogleCredential(response) {
  message.classList.remove('show', 'error');
  try {
    const r = await fetch(`${API_BASE}/api/auth/google`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id_token: response.credential }),
    });
    const data = await r.json().catch(() => null);
    if (r.status === 409) {
      showMessage('An account with this email already exists. Please log in instead.', true);
      return;
    }
    if (r.status === 401) {
      showMessage('Google sign-in failed: Google rejected this account. Try another account.', true);
      return;
    }
    if (!r.ok) {
      showMessage('Google sign-in failed. Try again.', true);
      return;
    }
    await completeLogin(data.access_token, null);
  } catch (err) {
    showMessage('We couldn’t connect. Check your internet connection and try again.', true);
  }
}

/* Google sign-in runs through the GIS-rendered button above (FedCM-safe). */
