/* =============================================================================
 * Smart Surf School — global front-end behaviour
 *
 * Deliberately small: HTMX handles server interaction and Alpine handles local
 * component state. This file only wires the two together and provides helpers
 * every screen needs (toasts, confirmations, chart defaults).
 * ============================================================================= */
(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // CSRF for every HTMX request.
  // The token is also set on <body hx-headers>, but a page served from cache can
  // carry a stale token, so it is refreshed from the cookie on each request.
  // ---------------------------------------------------------------------------
  function getCookie(name) {
    const match = document.cookie.match(new RegExp('(^|;\\s*)' + name + '=([^;]*)'));
    return match ? decodeURIComponent(match[2]) : null;
  }

  document.body.addEventListener('htmx:configRequest', function (event) {
    const token = getCookie('csrftoken');
    if (token) {
      event.detail.headers['X-CSRFToken'] = token;
    }
  });

  // ---------------------------------------------------------------------------
  // Toasts
  // ---------------------------------------------------------------------------
  const TOAST_STYLES = {
    success: 'border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-500/30 dark:bg-emerald-900 dark:text-emerald-100',
    error: 'border-rose-200 bg-rose-50 text-rose-900 dark:border-rose-500/30 dark:bg-rose-900 dark:text-rose-100',
    warning: 'border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-900 dark:text-amber-100',
    info: 'border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-500/30 dark:bg-sky-900 dark:text-sky-100',
  };

  function showToast(message, level) {
    const region = document.getElementById('toast-region');
    if (!region) return;

    const toast = document.createElement('div');
    toast.className =
      'pointer-events-auto flex items-start gap-3 rounded-lg border px-4 py-3 text-sm shadow-lg ' +
      'animate-slide-in ' + (TOAST_STYLES[level] || TOAST_STYLES.info);
    toast.setAttribute('role', level === 'error' ? 'alert' : 'status');

    const text = document.createElement('div');
    text.className = 'flex-1';
    text.textContent = message;           // textContent, never innerHTML
    toast.appendChild(text);

    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'shrink-0 opacity-60 hover:opacity-100';
    close.textContent = '×';
    close.setAttribute('aria-label', 'Close');
    close.addEventListener('click', () => toast.remove());
    toast.appendChild(close);

    region.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 200);
    }, level === 'error' ? 8000 : 4000);
  }

  window.surfToast = showToast;

  // Server-triggered toasts: respond with HX-Trigger: {"toast": {...}}
  document.body.addEventListener('toast', function (event) {
    const detail = event.detail || {};
    showToast(detail.message || detail.value || '', detail.level || 'info');
  });

  // ---------------------------------------------------------------------------
  // HTMX error handling — a failed request must never fail silently.
  // ---------------------------------------------------------------------------
  document.body.addEventListener('htmx:responseError', function (event) {
    const status = event.detail.xhr.status;
    let message = 'Request failed (' + status + ').';
    try {
      const body = JSON.parse(event.detail.xhr.responseText);
      if (body && body.error && body.error.message) message = body.error.message;
    } catch (e) { /* not JSON — keep the generic message */ }

    if (status === 403) message = 'You do not have permission to do that.';
    if (status === 404) message = 'Not found.';
    if (status >= 500) message = 'Server error. The incident has been logged.';
    showToast(message, 'error');
  });

  document.body.addEventListener('htmx:sendError', function () {
    showToast('Network unavailable. Check your connection.', 'error');
  });

  document.body.addEventListener('htmx:timeout', function () {
    showToast('The request timed out.', 'warning');
  });

  // ---------------------------------------------------------------------------
  // Destructive-action confirmation: <button hx-confirm-danger="Delete this?">
  // ---------------------------------------------------------------------------
  document.body.addEventListener('htmx:confirm', function (event) {
    const question = event.detail.elt.getAttribute('hx-confirm-danger');
    if (!question) return;
    event.preventDefault();
    if (window.confirm(question)) event.detail.issueRequest();
  });

  // ---------------------------------------------------------------------------
  // Chart.js shared defaults (loaded only on pages that include the library).
  // ---------------------------------------------------------------------------
  window.surfChartDefaults = function () {
    const dark = document.documentElement.classList.contains('dark');
    const grid = dark ? 'rgba(148,163,184,0.15)' : 'rgba(100,116,139,0.15)';
    const text = dark ? '#cbd5e1' : '#475569';
    return {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { labels: { color: text, usePointStyle: true, boxWidth: 8, font: { size: 11 } } },
        tooltip: {
          backgroundColor: dark ? '#1e293b' : '#0f172a',
          padding: 10,
          cornerRadius: 8,
          titleFont: { size: 12 },
          bodyFont: { size: 12 },
        },
      },
      scales: {
        x: { grid: { color: grid, drawBorder: false }, ticks: { color: text, font: { size: 11 } } },
        y: { grid: { color: grid, drawBorder: false }, ticks: { color: text, font: { size: 11 } }, beginAtZero: true },
      },
    };
  };

  /** Brand-consistent categorical palette, readable in both themes. */
  window.surfChartColors = [
    '#0083ce', '#10b981', '#f59e0b', '#f43f5e',
    '#8b5cf6', '#06b6d4', '#84cc16', '#ec4899',
  ];

  // ---------------------------------------------------------------------------
  // Keyboard shortcuts
  // ---------------------------------------------------------------------------
  document.addEventListener('keydown', function (event) {
    const inField = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)
      || document.activeElement.isContentEditable;

    // "/" focuses global search
    if (event.key === '/' && !inField) {
      const search = document.querySelector('input[type="search"][name="q"]');
      if (search) { event.preventDefault(); search.focus(); }
    }
    // Escape closes any open modal region
    if (event.key === 'Escape') {
      const modal = document.getElementById('modal-region');
      if (modal && modal.innerHTML.trim()) modal.innerHTML = '';
    }
  });
})();
