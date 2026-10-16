/* QueueFlow AI — Owner Dashboard (wired to FastAPI backend S2–S5).
 *
 * MVP scope notes (honest limits, same as backend):
 * - Recorded VIDEO analysis only. Image mode is preview-only; the backend
 *   exposes POST .../analyze-video and no image endpoint (future gap).
 * - No deactivate-virtual-queue endpoint exists; Deactivate closes the
 *   session via PATCH settings {status:'closed'} and starts a fresh one.
 * - No QR-image endpoint exists (library undecided); the join API URL is
 *   shown/copied, and the customer tracking page is the next frontend step.
 * - Vision model (ultralytics/YOLO) may be uninstalled on the server; the
 *   API then answers 503 and we surface its message verbatim.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  // Same-origin when served by the backend (http), localhost fallback for file://.
  const API_BASE = (localStorage.getItem('queueflow_api') ||
    (window.location.protocol.indexOf('http') === 0 ? window.location.origin : 'http://127.0.0.1:8000')
  ).replace(/\/$/, '');

  let mode = 'image';
  let file = null;
  let objectUrl = null;
  let sessionId = Number(localStorage.getItem('queueflow_session') || 0) || null;
  let lastQrSession = null; // session id whose QR image is rendered
  let lastQrBlobUrl = null; // object URL for the fetched QR PNG (revoked on switch)
  const QR_PLACEHOLDER = $('qrVisual').innerHTML; // static art + lock, restored when inactive

  // D2 fix: <img> requests carry no Authorization header, and the QR
  // endpoint is owner-guarded (401 without token -> broken image). So the
  // PNG is fetched with the JWT and shown via a blob URL instead.
  async function loadQrImage(sessionId) {
    const token = getToken();
    try {
      const res = await fetch(`${API_BASE}/api/queue-sessions/${sessionId}/qr`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) return; // keep placeholder; the copyable link still works
      const url = URL.createObjectURL(await res.blob());
      if (lastQrSession !== sessionId) { URL.revokeObjectURL(url); return; }
      if (lastQrBlobUrl) URL.revokeObjectURL(lastQrBlobUrl);
      lastQrBlobUrl = url;
      $('qrVisual').innerHTML =
        `<img src="${url}" alt="Queue join QR code" style="width:98px;height:98px">`;
    } catch (_) { /* keep placeholder; the copyable link still works */ }
  }

  // ---------- helpers ----------
  function getToken() {
    return localStorage.getItem('queueflow_token') || sessionStorage.getItem('queueflow_token');
  }

  // All dashboard feedback flows through the shared premium Notify system
  // (assets/js/notify.js). Inline analysis notes stay on the page as the
  // persistent record; toasts are the transient announcement. Guarded: if
  // the shared lib hasn't loaded (stale cache), nothing throws.
  function toast(msg, kind) {
    if (window.Notify) { Notify.show(msg, { kind: kind || 'info' }); return; }
    try { console.warn('[toast]', kind || 'info', msg); } catch (_) {}
  }

  function apiDetail(data, fallback) {
    if (!data) return fallback;
    if (typeof data.detail === 'string') return data.detail;
    if (Array.isArray(data.detail) && data.detail.length > 0) {
      const first = data.detail[0];
      if (typeof first === 'string') return first;
      if (first && first.msg) return String(first.msg);
    }
    return fallback;
  }

  // Escape server text before innerHTML interpolation (defense in depth:
  // backend details are curated, but markup must never execute).
  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    let res;
    try {
      res = await fetch(`${API_BASE}${path}`, { ...options, headers });
    } catch (err) {
      return { ok: false, network: true, status: 0, data: null };
    }
    const data = await res.json().catch(() => null);
    if (res.status === 401) {
      // Token missing/invalid/expired: back to login (same as auth guard).
      sessionStorage.removeItem('queueflow_token');
      localStorage.removeItem('queueflow_token');
      window.location.href = 'login.html';
      return { ok: false, status: 401, data, auth: true };
    }
    return { ok: res.ok, status: res.status, data };
  }

  function fmtWhen(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString();
  }

  // ---------- navigation ----------
  function showPage(page) {
    document.querySelectorAll('.page').forEach((p) => p.classList.remove('active'));
    document.querySelectorAll('.nav[data-page]').forEach((n) =>
      n.classList.toggle('active', n.dataset.page === page)
    );
    $(page === 'upload' ? 'uploadPage' : 'dashboardPage').classList.add('active');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  document.querySelectorAll('.nav[data-page]').forEach((a) =>
    a.addEventListener('click', (e) => { e.preventDefault(); showPage(a.dataset.page); })
  );
  $('viewUploadBtn').addEventListener('click', () => showPage('upload'));

  // ---------- queue active visuals ----------
  function setQueueActive(active) {
    $('queueStatusMetric').textContent = active ? 'Active' : 'Not active';
    $('queueStatusMetric').style.color = active ? 'var(--green)' : 'var(--muted)';
    $('queueStatusFoot').textContent = active ? 'Customers can join via QR' : 'Activate to let customers join';
    $('queueStatePill').textContent = active ? 'Active' : 'Not active';
    const qrLockEl = $('qrLock');
    // Null-safe: when the real QR <img> is rendered, the lock node is gone.
    if (qrLockEl) qrLockEl.style.display = active ? 'none' : 'grid';
    $('qrTitle').textContent = active ? 'Queue is active' : 'Queue not active';
    $('qrDescription').textContent = active
      ? 'Customers can scan the QR code or open the join link to take a token.'
      : 'Upload and analyze the queue, then activate it to make the customer link available.';
    $('activateBtn').disabled = active;
    $('deactivateBtn').disabled = !active;
  }

  // ---------- status + tokens render ----------
  function renderStatus(st) {
    const s = st.session;
    $('sessionPill').textContent = `Session #${s.id} · ${s.status}${s.virtual_queue_enabled ? ' · virtual ON' : ''} · ${st.tokens_issued}/${st.token_capacity} tokens`;
    $('peopleMetric').textContent = s.physical_people_remaining;
    $('peopleFoot').textContent =
      `Vision estimate ${s.people_count ?? '—'} (reviewable) · capacity ${st.token_capacity} · ${st.tokens_remaining} left`;
    $('waitingMetric').textContent = st.waiting_virtual_count;
    $('waitMetric').textContent = st.estimated_wait_minutes == null ? '—' : `~${st.estimated_wait_minutes} min`;
    $('lastAnalyzed').textContent = fmtWhen(s.updated_at);
    $('latestCount').textContent = s.people_count ?? '—';
    $('analysisStatus').textContent = s.people_count == null
      ? 'Upload a video to start analysis.'
      : `Vision estimate ${s.people_count} (approx). Confirm the count on the Upload page.`;
    setQueueActive(s.virtual_queue_enabled);
    const origin = window.location.protocol.indexOf('http') === 0 ? window.location.origin : API_BASE;
    $('joinLink').value = s.virtual_queue_enabled
      ? `${origin}/join.html?session=${s.id}`
      : 'Available after activation';
    if (s.virtual_queue_enabled) {
      // D2: real scannable QR image for THIS session (backend PNG, fetched
      // with auth — see loadQrImage). Rendered once per session; the
      // lock/placeholder returns when off.
      if (lastQrSession !== s.id) {
        lastQrSession = s.id;
        loadQrImage(s.id);
      }
    } else if (lastQrSession !== null) {
      if (lastQrBlobUrl) { URL.revokeObjectURL(lastQrBlobUrl); lastQrBlobUrl = null; }
      $('qrVisual').innerHTML = QR_PLACEHOLDER;
      lastQrSession = null;
    }
    $('qrFoot').textContent = s.virtual_queue_enabled
      ? 'Customers open this link (or scan its QR) to join and track their token.'
      : 'Real QR generation requires backend integration.';
    const updatedAt = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    if (st.virtual_queue_recommended && !s.virtual_queue_enabled) {
      $('refreshLabel').textContent = `Heavy queue (${st.people_ahead} ahead) — consider activating · ${updatedAt}`;
    } else {
      $('refreshLabel').textContent = `Live from backend · ${updatedAt}`;
    }
    // Prefill owner-review inputs with current authoritative values.
    if (document.activeElement !== $('correctedCount') && $('correctedCount').value === '') {
      $('correctedCount').placeholder = `Vision estimate: ${s.people_count ?? '—'}`;
    }
    $('avgServiceInput').placeholder = String(s.average_service_minutes);
  }

  // (Virtual Tokens card removed per owner decision — token controls live here next.)

  function renderTokens(list) {
    $('issuedMetric').textContent = list.length;
    // Flow preview: now-serving = first called else first waiting; then next waiting.
    const active = list.filter((t) => t.status === 'waiting' || t.status === 'called');
    if (active.length === 0) {
      $('flowTrack').innerHTML = '<div class="flow-token current"><strong>—</strong><small>No active token</small></div><span class="flow-arrow">›</span><div class="flow-token"><strong>—</strong><small>Waiting for data</small></div><span class="flow-arrow">›</span><div class="flow-token"><strong>—</strong><small>Waiting for data</small></div><span class="flow-arrow">›</span><div class="flow-more"><strong>···</strong><small>and more</small></div>';
      return;
    }
    const now = active.find((t) => t.status === 'called') || active[0];
    const rest = active.filter((t) => t.id !== now.id).slice(0, 2);
    const more = active.length - 1 - rest.length;
    let html = `<div class="flow-token current"><strong>#${now.token_number}</strong><small>${now.status}</small></div>`;
    rest.forEach((t) => {
      html += `<span class="flow-arrow">›</span><div class="flow-token"><strong>#${t.token_number}</strong><small>${t.status}</small></div>`;
    });
    html += `<span class="flow-arrow">›</span><div class="flow-more"><strong>${more > 0 ? `+${more}` : '···'}</strong><small>${more > 0 ? 'more waiting' : 'and more'}</small></div>`;
    $('flowTrack').innerHTML = html;
  }

  async function refreshAll(quiet = false) {
    if (!sessionId) return;
    const [stRes, tkRes] = await Promise.all([
      api(`/api/queue-sessions/${sessionId}/status`),
      api(`/api/queue-sessions/${sessionId}/tokens`),
    ]);
    if (!stRes.ok) {
      if (stRes.network) {
        // Quiet background polls must never toast-storm when the backend
        // goes away mid-shift; manual Refresh still reports it.
        if (!quiet) toast('We couldn’t reach the server. Start the backend and try again.', 'error');
      } else if (!stRes.auth) toast('Could not load queue status.', 'error');
      return;
    }
    renderStatus(stRes.data);
    if (tkRes.ok) renderTokens(tkRes.data);
  }

  async function setTokenStatus(tokenId, next) {
    const r = await api(`/api/owner/tokens/${tokenId}/status`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: next }),
    });
    if (!r.ok) {
      if (r.network) toast('We couldn’t reach the server. Try again.', 'error');
      else if (!r.auth) toast('Could not update the token.', 'error');
      return;
    }
    toast(`Token marked ${next}.`, 'success');
    refreshAll();
  }

  // ---------- upload: mode, preview, analyze ----------
  const imageIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>';
  const videoIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="13" height="14" rx="2"/><path d="m16 10 5-3v10l-5-3z"/></svg>';

  function resetUploadResults() {
    $('uploadPeople').textContent = '—';
    $('uploadWait').textContent = '—';
    $('uploadLength').textContent = '—';
    $('uploadAnalysisBadge').textContent = 'NOT ANALYZED';
    $('analysisNotes').innerHTML = '<li>Upload an image or recorded video to begin.</li><li>Both are analyzed by the backend (YOLO person detection).</li>';
  }

  function clearFile() {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
    file = null;
    $('fileInput').value = '';
    $('previewFile').classList.remove('show');
    $('previewFile').textContent = '';
    $('previewArea').innerHTML = '<div class="preview-empty">Your selected queue image or video will appear here.</div>';
    resetUploadResults();
  }

  function setMode(next) {
    mode = next;
    $('imageMode').classList.toggle('active', mode === 'image');
    $('videoMode').classList.toggle('active', mode === 'video');
    $('fileInput').accept = mode === 'image' ? 'image/jpeg,image/png,image/webp' : 'video/mp4,video/webm,video/quicktime';
    $('dropIcon').innerHTML = mode === 'image' ? imageIcon : videoIcon;
    $('dropTitle').textContent = mode === 'image' ? 'Drag & drop an image here' : 'Drag & drop a recorded video here';
    $('fileHelp').textContent = mode === 'image' ? 'Supports JPG, PNG, WEBP · Max 10 MB' : 'Supports MP4, WEBM, MOV · Max 50 MB';
    $('dropzone').setAttribute('aria-label', mode === 'image' ? 'Select queue image' : 'Select recorded video');
    clearFile();
  }

  function acceptFile(f) {
    if (!f) return;
    const ok = mode === 'image'
      ? ['image/jpeg', 'image/png', 'image/webp'].includes(f.type)
      : ['video/mp4', 'video/webm', 'video/quicktime'].includes(f.type);
    if (!ok) { toast('Choose a supported file. Images: JPG, PNG, WEBP (≤10 MB). Video: MP4, WEBM, MOV (≤50 MB).', 'warning'); return; }
    const max = mode === 'image' ? 10 * 1048576 : 50 * 1048576;
    if (f.size > max) { toast('That file is too large. Images ≤10 MB, video ≤50 MB.', 'warning'); return; }
    clearFile();
    file = f;
    objectUrl = URL.createObjectURL(f);
    const media = mode === 'image'
      ? `<img class="preview-image" src="${objectUrl}" alt="Selected queue image">`
      : `<video class="preview-image" src="${objectUrl}" controls preload="metadata"></video>`;
    $('previewArea').innerHTML = media;
    $('previewFile').textContent = `Selected: ${f.name} · ${(f.size / 1048576).toFixed(2)} MB`;
    $('previewFile').classList.add('show');
    toast('File selected. Click Analyze Queue to continue.', 'info');
  }

  $('imageMode').addEventListener('click', () => setMode('image'));
  $('videoMode').addEventListener('click', () => setMode('video'));
  $('browseBtn').addEventListener('click', (e) => { e.stopPropagation(); $('fileInput').click(); });
  $('changeFileBtn').addEventListener('click', () => $('fileInput').click());
  $('dropzone').addEventListener('click', (e) => { if (e.target !== $('browseBtn')) $('fileInput').click(); });
  $('dropzone').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('fileInput').click(); }
  });
  $('fileInput').addEventListener('change', (e) => acceptFile(e.target.files[0]));
  ['dragenter', 'dragover'].forEach((ev) => $('dropzone').addEventListener(ev, (e) => {
    e.preventDefault(); $('dropzone').classList.add('drag');
  }));
  ['dragleave', 'drop'].forEach((ev) => $('dropzone').addEventListener(ev, (e) => {
    e.preventDefault(); $('dropzone').classList.remove('drag');
  }));
  $('dropzone').addEventListener('drop', (e) => acceptFile(e.dataTransfer.files[0]));
  $('clearBtn').addEventListener('click', clearFile);

  $('analyzeBtn').addEventListener('click', async () => {
    if (!file) { toast('Upload an image or recorded video first.', 'warning'); return; }
    if (!sessionId) { toast('No queue session yet. Create one first.', 'warning'); return; }
    const endpoint = mode === 'image' ? 'analyze-image' : 'analyze-video';
    $('analyzeBtn').disabled = true;
    // Genuinely slow (CPU model, seconds): sticky loading toast, dismissed below.
    const analyzing = window.Notify
      ? Notify.loading(mode === 'image' ? 'Analyzing image…' : 'Analyzing video…')
      : null;
    try {
      const form = new FormData();
      form.append('file', file, file.name);
      const token = getToken();
      let res;
      try {
        res = await fetch(`${API_BASE}/api/queue-sessions/${sessionId}/${endpoint}`, {
          method: 'POST',
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          body: form,
        });
      } catch (err) {
        toast('We couldn’t reach the server. Try again.', 'error');
        return;
      }
      const data = await res.json().catch(() => null);
      if (res.status === 401) {
        window.location.href = 'login.html';
        return;
      }
      if (!res.ok) {
        $('uploadAnalysisBadge').textContent = res.status === 503 ? 'MODEL MISSING' : 'ANALYSIS FAILED';
        $('analysisNotes').innerHTML =
          `<li>${esc(apiDetail(data, `Analysis failed (HTTP ${res.status}).`))}</li>` +
          (res.status === 503 ? '<li>Ask the backend host to install the vision model, then retry.</li>' : '<li>Check the file and try again.</li>');
        toast('We couldn’t analyze this file. Please try again.', 'error');
        return;
      }
      $('uploadPeople').textContent = data.estimated_people;
      $('uploadAnalysisBadge').textContent = 'NEEDS REVIEW';
      $('analysisNotes').innerHTML =
        `<li>~${data.estimated_people} people detected (${data.method}, ` +
        `${data.frames_sampled} sampled frame${data.frames_sampled === 1 ? '' : 's'}).</li>` +
        `<li>Confidence ${data.confidence_threshold}. ${data.estimate_note}</li>` +
        '<li>Confirm the count below, then Apply — analysis never confirms the queue by itself.</li>';
      $('correctedCount').value = data.estimated_people;
      toast('Analysis done. Review the count and press Apply.', 'success');
      refreshAll();
    } finally {
      if (analyzing) analyzing.dismiss();
      $('analyzeBtn').disabled = false;
    }
  });

  // ---------- owner review + settings (FR-05/FR-06) ----------
  $('applyReviewBtn').addEventListener('click', async () => {
    if (!sessionId) { toast('No queue session yet.', 'warning'); return; }
    const patch = {};
    const countRaw = $('correctedCount').value;
    const avgRaw = $('avgServiceInput').value;
    if (countRaw !== '') {
      const v = Number(countRaw);
      if (!Number.isInteger(v) || v < 0) { toast('Confirmed count must be a whole number ≥ 0.', 'warning'); return; }
      patch.physical_people_remaining = v;
      patch.people_count = v;
    }
    if (avgRaw !== '') {
      const a = Number(avgRaw);
      if (!(a >= 0) || a > 1440) { toast('Average minutes must be 0–1440.', 'warning'); return; }
      patch.average_service_minutes = a;
    }
    if (Object.keys(patch).length === 0) { toast('Enter a confirmed count or average time first.', 'warning'); return; }
    const r = await api(`/api/queue-sessions/${sessionId}/settings`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!r.ok) {
      if (r.network) toast('We couldn’t reach the server. Try again.', 'error');
      else if (!r.auth) toast('Could not apply settings.', 'error');
      return;
    }
    $('correctedCount').value = '';
    $('avgServiceInput').value = '';
    toast('Queue settings applied. Estimates updated.', 'success');
    refreshAll();
  });

  // ---------- activation (explicit owner action; system only recommends) ----------
  $('activateBtn').addEventListener('click', async () => {
    if (!sessionId) { toast('No queue session yet.', 'warning'); return; }
    const r = await api(`/api/queue-sessions/${sessionId}/activate-virtual-queue`, { method: 'POST' });
    if (!r.ok) {
      if (r.network) toast('We couldn’t reach the server. Try again.', 'error');
      else if (!r.auth) toast('Could not activate the queue.', 'error');
      return;
    }
    toast('Virtual queue is now active.', 'success');
    refreshAll();
  });

  $('deactivateBtn').addEventListener('click', async () => {
    // E3: stops new joins only. Session stays open, existing tokens keep
    // tracking, and the owner can re-activate any time. Distinct from
    // closing the session (PATCH settings status).
    if (!sessionId) return;
    const r = await api(`/api/queue-sessions/${sessionId}/deactivate-virtual-queue`, { method: 'POST' });
    if (!r.ok) {
      if (r.network) toast('We couldn’t reach the server. Try again.', 'error');
      else if (!r.auth) toast('Could not stop new joins.', 'error');
      return;
    }
    toast('New customers can no longer join. Existing tokens remain active.', 'success');
    refreshAll();
  });

  $('newSessionBtn').addEventListener('click', async () => {
    const r = await api('/api/queue-sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active_counters: 1, average_service_minutes: 5.0, physical_people_remaining: 0 }),
    });
    if (!r.ok) {
      if (r.network) toast('We couldn’t reach the server. Try again.', 'error');
      else if (!r.auth) toast('Could not create a session.', 'error');
      return;
    }
    sessionId = r.data.id;
    localStorage.setItem('queueflow_session', String(sessionId));
    toast(`Session #${sessionId} created.`, 'success');
    refreshAll();
  });

  $('refreshBtn').addEventListener('click', refreshAll);

  $('copyLink').addEventListener('click', async () => {
    const v = $('joinLink').value;
    if (!v || v === 'Available after activation') {
      toast('The customer join link will be available after backend activation.', 'info');
      return;
    }
    try {
      await navigator.clipboard.writeText(v);
      toast('Join link copied.', 'success');
    } catch (err) {
      $('joinLink').select();
      toast('Copy manually: link selected.', 'info');
    }
  });

  // ---------- boot: guard, owner, session ----------
  async function bootSession() {
    const r = await api('/api/queue-sessions');
    if (!r.ok) {
      if (r.network) {
        $('sessionPill').textContent = 'Backend offline';
        toast('Cannot reach the API. Start the backend and try again.', 'error');
      }
      return;
    }
    const open = r.data.find((s) => s.status === 'open');
    if (open) {
      sessionId = open.id;
    } else {
      const c = await api('/api/queue-sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active_counters: 1, average_service_minutes: 5.0, physical_people_remaining: 0 }),
      });
      if (!c.ok) {
        if (!c.auth) toast('Could not create a queue session.', 'error');
        return;
      }
      sessionId = c.data.id;
    }
    localStorage.setItem('queueflow_session', String(sessionId));
    refreshAll();
  }

  (async function boot() {
    if (!getToken()) { window.location.href = 'login.html'; return; }
    $('dateLabel').textContent = new Date().toLocaleDateString(undefined, {
      weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
    });
    $('timeLabel').textContent = new Date().toLocaleTimeString(undefined, {
      hour: '2-digit', minute: '2-digit',
    }) + ' · Owner session';
    const me = await api('/api/auth/me');
    if (me.ok && me.data) {
      const shop = me.data.shop_name || 'Owner';
      $('ownerName').textContent = shop;
      $('ownerAvatar').textContent = shop.trim().charAt(0).toUpperCase() || 'O';
      $('welcomeTitle').textContent = `Welcome back, ${shop}`;
    }
    $('backendNotice').textContent =
      'Connected mode: image + video analysis, counts, waits, activation and tokens all use the FastAPI backend. Estimates stay approximate.';
    setQueueActive(false);
    await bootSession();
    // D3: keep owner numbers fresh while the page is open (quiet: no
    // offline toast storms if the backend goes away mid-shift).
    if (sessionId) setInterval(() => { refreshAll(true); }, 15000);
  })();
})();
