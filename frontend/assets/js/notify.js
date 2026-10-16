/* QueueFlow AI — shared premium toast notifications.
 *
 * One component for every page (signup, login, dashboard, join, status).
 * No dependencies, no framework. Include after nothing in particular but
 * BEFORE the page script (both use `defer`, document order is preserved).
 *
 *   Notify.success('Saved.')                  // auto-dismiss ~4s
 *   Notify.error('Could not save.')            // stays ~9s, role=alert
 *   Notify.warning('Check this file.')         // stays ~6s
 *   Notify.info('Queue link copied.')          // auto-dismiss ~4s
 *   const job = Notify.loading('Analyzing…');  // sticky until dismissed
 *   job.update('Still working…'); job.dismiss();
 *   Notify.show('Hi', { kind: 'info', title: 'Hello', duration: 0 });
 *   Notify.dismiss(id); Notify.clear();
 *
 * Design contract (Apple-restrained): white translucent surface, thin
 * border, soft shadow, 14px radius, tinted status icon, title optional,
 * auto-dismiss pauses on hover/focus, identical messages dedupe instead
 * of stacking, `prefers-reduced-motion` disables animation.
 */
(function () {
  'use strict';

  var MAX_VISIBLE = 4;
  var DEDUPE_WINDOW_MS = 2500;
  var EXIT_MS = 230;
  var DURATIONS = { success: 4200, info: 4200, warning: 6000, error: 9000, loading: 0 };

  var ICONS = {
    success: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/><path d="m8.5 12.5 2.5 2.5 4.5-5.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    error: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/><path d="M9.5 9.5l5 5M14.5 9.5l-5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    warning: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 4.5 20.5 19.5h-17L12 4.5Z" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M12 10v4.2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="17.2" r="1.1" fill="currentColor"/></svg>',
    info: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/><path d="M12 11v5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="12" cy="7.8" r="1.1" fill="currentColor"/></svg>',
    loading: '<span class="notify-spinner" aria-hidden="true"></span>'
  };

  var seq = 0;
  var timers = {}; // id -> { timeout, remaining, startedAt, duration }
  var recent = []; // [{ key, at }] for dedupe

  function kindOf(kind) {
    return kind === 'success' || kind === 'error' || kind === 'warning' ||
      kind === 'loading' ? kind : 'info';
  }

  function ensureWrap() {
    var wrap = document.getElementById('notifyWrap');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.id = 'notifyWrap';
      wrap.className = 'notify-wrap';
      wrap.setAttribute('aria-live', 'polite');
      document.body.appendChild(wrap);
    }
    return wrap;
  }

  function prune(now) {
    recent = recent.filter(function (r) { return now - r.at < DEDUPE_WINDOW_MS; });
  }

  function arm(id, el, duration) {
    clear(id);
    if (!duration || duration <= 0) return;
    timers[id] = {
      duration: duration, remaining: duration, startedAt: Date.now(), timeout: null
    };
    timers[id].timeout = setTimeout(function () { dismiss(id); }, duration);
  }

  function clear(id) {
    if (timers[id] && timers[id].timeout) clearTimeout(timers[id].timeout);
    delete timers[id];
  }

  function pause(id) {
    var t = timers[id];
    if (!t || !t.timeout) return;
    clearTimeout(t.timeout);
    t.timeout = null;
    t.remaining = Math.max(0, t.remaining - (Date.now() - t.startedAt));
  }

  function resume(id) {
    var t = timers[id];
    if (!t || t.timeout || t.remaining <= 0) return;
    t.startedAt = Date.now();
    t.timeout = setTimeout(function () { dismiss(id); }, t.remaining);
  }

  function show(message, options) {
    options = options || {};
    var kind = kindOf(options.kind);
    var text = message == null ? '' : String(message);
    if (!text) return null;
    var title = options.title ? String(options.title) : null;
    var duration = options.duration === undefined
      ? DURATIONS[kind] : Number(options.duration) || 0;

    var now = Date.now();
    prune(now);
    var key = kind + '|' + (title || '') + '|' + text;
    var wrap = ensureWrap();
    var dup = null;
    Array.prototype.forEach.call(wrap.children, function (child) {
      if (child.getAttribute('data-key') === key) dup = child;
    });
    var fresh = recent.filter(function (r) { return r.key === key; }).length === 0;
    if (dup) {
      // Same notification already on screen: refresh its timer, no stacking.
      arm(dup.getAttribute('data-id'), dup, duration);
      return dup.getAttribute('data-id');
    }
    if (!fresh) return null;

    seq += 1;
    var id = 'n' + seq + '-' + now.toString(36);
    var el = document.createElement('div');
    el.className = 'notify notify-' + kind;
    el.setAttribute('data-id', id);
    el.setAttribute('data-key', key);
    el.setAttribute('role', kind === 'error' ? 'alert' : 'status');

    var icon = document.createElement('span');
    icon.className = 'notify-icon';
    icon.innerHTML = ICONS[kind];
    el.appendChild(icon);

    var body = document.createElement('div');
    body.className = 'notify-body';
    if (title) {
      var h = document.createElement('div');
      h.className = 'notify-title';
      h.textContent = title;
      body.appendChild(h);
    }
    var p = document.createElement('div');
    p.className = 'notify-text';
    p.textContent = text;
    body.appendChild(p);
    el.appendChild(body);

    var close = document.createElement('button');
    close.type = 'button';
    close.className = 'notify-close';
    close.setAttribute('aria-label', 'Dismiss notification');
    close.innerHTML = '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M7 7l10 10M17 7L7 17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
    close.addEventListener('click', function () { dismiss(id); });
    el.appendChild(close);

    el.addEventListener('mouseenter', function () { pause(id); });
    el.addEventListener('mouseleave', function () { resume(id); });
    el.addEventListener('focusin', function () { pause(id); });
    el.addEventListener('focusout', function () { resume(id); });

    while (wrap.children.length >= MAX_VISIBLE) {
      var oldest = wrap.firstChild;
      if (oldest.classList.contains('notify-loading')) break; // never evict active work
      wrap.removeChild(oldest);
    }
    wrap.appendChild(el);
    recent.push({ key: key, at: now });
    arm(id, el, duration);
    return id;
  }

  function dismiss(id) {
    clear(id);
    var wrap = document.getElementById('notifyWrap');
    if (!wrap) return;
    var el = wrap.querySelector('[data-id="' + id + '"]');
    if (!el) return;
    el.classList.add('leaving');
    setTimeout(function () {
      if (el.parentNode) el.parentNode.removeChild(el);
      if (!wrap.children.length && wrap.parentNode) wrap.parentNode.removeChild(wrap);
    }, EXIT_MS);
  }

  function loading(message, options) {
    options = options || {};
    options.kind = 'loading';
    if (options.duration === undefined) options.duration = 0;
    var id = show(message, options);
    return {
      id: id,
      dismiss: function () { dismiss(id); },
      update: function (msg) {
        var wrap = document.getElementById('notifyWrap');
        var el = wrap && wrap.querySelector('[data-id="' + id + '"] .notify-text');
        if (el) el.textContent = msg;
      }
    };
  }

  window.Notify = {
    show: show,
    success: function (m, o) { o = o || {}; o.kind = 'success'; return show(m, o); },
    info: function (m, o) { o = o || {}; o.kind = 'info'; return show(m, o); },
    warning: function (m, o) { o = o || {}; o.kind = 'warning'; return show(m, o); },
    error: function (m, o) { o = o || {}; o.kind = 'error'; return show(m, o); },
    loading: loading,
    dismiss: dismiss,
    clear: function () {
      var wrap = document.getElementById('notifyWrap');
      if (!wrap) return;
      Object.keys(timers).forEach(clear);
      wrap.innerHTML = '';
      if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
    }
  };
})();
