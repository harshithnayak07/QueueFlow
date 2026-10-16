const form = document.getElementById('signupForm');
const notice = document.getElementById('notice');
const submitBtn = form.querySelector('.submit');

// Same-origin when served by the backend (http), localhost fallback for file://.
// Override with localStorage.setItem('queueflow_api', 'http://host:port') if needed.
const API_BASE = (localStorage.getItem('queueflow_api') ||
  (window.location.protocol.indexOf('http') === 0 ? window.location.origin : 'http://127.0.0.1:8000')
).replace(/\/$/, '');

// Must match backend settings.google_client_id (public identifier).
const GOOGLE_CLIENT_ID = '650223765036-q2s9vdbg607mett9ld348plp46s36p54.apps.googleusercontent.com';

// Shared premium notifications (assets/js/notify.js): one component for
// every page — variants, auto-dismiss, dedupe, reduced-motion + ARIA live.
// (Position/styling now lives in main.css `.notify-*`, top-right desktop.)
// Guarded: if the shared lib hasn't loaded (stale cache), the inline
// notice above still shows and nothing throws.
function popup(msg, kind) {
  if (window.Notify) { Notify.show(msg, { kind: kind || 'info' }); return; }
  try { console.warn('[notify]', kind || 'info', msg); } catch (_) {}
}

document.querySelectorAll('[data-toggle]').forEach((btn) => btn.addEventListener('click', () => {
  const input = document.getElementById(btn.dataset.toggle);
  input.type = input.type === 'password' ? 'text' : 'password';
  btn.setAttribute('aria-label', input.type === 'password' ? 'Show password' : 'Hide password');
}));

function setError(name, invalid) {
  const field = document.querySelector(`[data-field="${name}"]`);
  if (field) field.classList.toggle('invalid', invalid);
}

function showNotice(msg, isError) {
  notice.textContent = msg;
  notice.classList.toggle('show', true);
  notice.classList.toggle('error', Boolean(isError));
  popup(msg, isError ? 'error' : 'success');
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  notice.classList.remove('show', 'error');

  const business = document.getElementById('business').value.trim();
  const owner = document.getElementById('owner').value.trim();
  const email = document.getElementById('email').value.trim();
  const phone = document.getElementById('phone').value.trim();
  const password = document.getElementById('password').value;
  const confirm = document.getElementById('confirm').value;

  const validEmail = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
  const validPhone = /^[+()\-\s\d]{7,20}$/.test(phone) && phone.replace(/\D/g, '').length >= 7;
  const checks = {
    business: !business,
    owner: !owner,
    email: !validEmail,
    phone: !validPhone,
    password: password.length < 8 || password.length > 72,
    confirm: confirm !== password || !confirm,
  };
  Object.entries(checks).forEach(([key, value]) => setError(key, value));
  if (Object.values(checks).some(Boolean) || !document.getElementById('terms').checked) {
    if (!document.getElementById('terms').checked) document.getElementById('terms').focus();
    else form.querySelector('.invalid input').focus();
    return;
  }

  // NOTE (MVP scope): backend OwnerRegister stores only shop_name/email/password.
  // Owner name + phone are validated UI-only and NOT sent. Label as future enhancement.
  const payload = { shop_name: business, email, password };

  submitBtn.disabled = true;
  const originalLabel = submitBtn.textContent;
  submitBtn.textContent = 'Creating account...';
  try {
    const regRes = await fetch(`${API_BASE}/api/auth/register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const regData = await regRes.json().catch(() => null);

    if (regRes.status === 409) {
      setError('email', true);
      showNotice('An account with this email already exists. Please log in instead.', true);
      return;
    }
    if (!regRes.ok) {
      showNotice('Could not create the account. Check your details and try again.', true);
      return;
    }

    // Auto-login so the owner gets a usable token right after signup.
    const loginRes = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    const loginData = await loginRes.json().catch(() => null);
    if (!loginRes.ok) {
      showNotice(`Account created for ${regData.email || email}. Auto-login failed — please use the Login page next.`, true);
      return;
    }
    localStorage.setItem('queueflow_token', loginData.access_token);
    localStorage.setItem('queueflow_email', regData.email || email);
    localStorage.setItem('queueflow_shop', regData.shop_name || business);
    showNotice(`Account created for ${regData.shop_name || business}. Signed in — opening your dashboard...`);
    setTimeout(() => { window.location.href = 'dashboard.html'; }, 1600);
  } catch (err) {
    showNotice('We couldn’t connect. Check your internet connection and try again.', true);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = originalLabel;
  }
});

document.getElementById('loginLink').addEventListener('click', (event) => {
  event.preventDefault();
  window.location.href = 'login.html';
});

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
    slot.innerHTML = '<p class="google-unavailable">Google sign-in needs internet. Email signup works without it.</p>';
  }
}

async function finishGoogleSignin(idToken) {
  const r = await fetch(`${API_BASE}/api/auth/google`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id_token: idToken }),
  });
  const data = await r.json().catch(() => null);
  if (r.status === 409) {
    // Owner decision: show the error and stop. No auto-redirect — the
    // owner goes to the Login page themselves when ready.
    showNotice('This email is already registered. Please log in.', true);
    return;
  }
  if (r.status === 401) {
    showNotice('Google sign-in failed: Google rejected this account. Try another account.', true);
    return;
  }
  if (!r.ok) {
      showNotice('Google sign-in failed. Try again.', true);
    return;
  }
  // New Google accounts are created by the backend (find-or-create by email).
  try {
    const meRes = await fetch(`${API_BASE}/api/auth/me`, {
      headers: { Authorization: `Bearer ${data.access_token}` },
    });
    if (meRes.ok) {
      const me = await meRes.json();
      if (me.shop_name) localStorage.setItem('queueflow_shop', me.shop_name);
      if (me.email) localStorage.setItem('queueflow_email', me.email);
    }
  } catch (_) { /* profile is a nice-to-have; sign-in already succeeded */ }
  localStorage.setItem('queueflow_token', data.access_token);
  showNotice('Account ready. Signed in with Google — opening your dashboard...');
  setTimeout(() => { window.location.href = 'dashboard.html'; }, 1600);
}

async function onGoogleCredential(response) {
  notice.classList.remove('show', 'error');
  try {
    await finishGoogleSignin(response.credential);
  } catch (err) {
    showNotice('We couldn’t connect. Check your internet connection and try again.', true);
  }
}

/* Google sign-in runs through the GIS-rendered button above (FedCM-safe). */
