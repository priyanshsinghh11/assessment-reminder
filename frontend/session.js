/*
 * The signed-in account, shared by both dashboards.
 *
 * Loaded BEFORE app.js and evaluations.js, because the first thing it does is
 * wrap window.fetch. Every request either page makes then carries its CSRF
 * token and knows what to do when the session has gone -- rather than each
 * page remembering, and one of them forgetting.
 *
 * WHAT THIS IS NOT. It is not the access rule. The server decides what an
 * account may see and refuses the rest; this only stops a hiring manager
 * being shown buttons that would answer 403, which is courtesy, not security.
 * If you are adding an admin-only feature, gate it on the server FIRST and
 * hide it here second.
 */

(function () {
  'use strict';

  const CSRF_COOKIE = 'ajaia_csrf';

  function cookie(name) {
    return document.cookie.split('; ')
      .filter((row) => row.startsWith(`${name}=`))
      .map((row) => decodeURIComponent(row.slice(name.length + 1)))[0] || '';
  }

  /* --- fetch, wrapped ---------------------------------------------------- */

  const nativeFetch = window.fetch.bind(window);

  window.fetch = function patchedFetch(input, init) {
    const options = Object.assign({}, init);
    const method = (options.method || 'GET').toUpperCase();
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    // Only our own endpoints. A token attached to a request to somewhere else
    // would be handing it to somewhere else.
    const sameOrigin = !/^https?:\/\//i.test(url)
      || url.startsWith(location.origin);

    if (sameOrigin && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      options.headers = Object.assign({}, options.headers,
        { 'X-CSRF-Token': cookie(CSRF_COOKIE) });
    }

    return nativeFetch(input, options).then((response) => {
      // A session that expired while the page sat open. Going back to the
      // login screen with `next` set is the whole recovery: they sign in and
      // land exactly where they were, rather than on a dashboard-shaped page
      // where every panel says "could not load".
      if (sameOrigin && response.status === 401 && !isLoginPage()) {
        const back = encodeURIComponent(location.pathname + location.search
          + location.hash);
        location.replace(`/login.html?next=${back}`);
      }
      return response;
    });
  };

  function isLoginPage() {
    return location.pathname === '/login.html';
  }

  /* --- who is signed in -------------------------------------------------- */

  const Session = {
    ready: null,
    user: null,
    authEnabled: true,
    roleCount: null,          // null = every role
    isAdmin: true,            // assumed until /api/auth/me answers otherwise
  };

  Session.ready = nativeFetch('/api/auth/me', {
    headers: { Accept: 'application/json' },
  }).then((r) => (r.ok ? r.json() : null)).then((data) => {
    if (!data) return Session;
    Session.authEnabled = data.auth_enabled !== false;
    Session.user = data.user;
    Session.roleCount = data.role_count;
    // With accounts switched off there is nobody to be, and the page is the
    // old undivided dashboard. See AUTH_ENABLED in config.py.
    Session.isAdmin = !Session.authEnabled
      || Boolean(data.user && data.user.is_admin);
    render();
    return Session;
  }).catch(() => Session);

  window.Session = Session;

  /* --- the chip in the header -------------------------------------------- */

  function initials(user) {
    const source = (user.name || user.email || '').trim();
    const parts = source.split(/[\s.@_-]+/).filter(Boolean);
    return ((parts[0] || '?')[0] + (parts[1] ? parts[1][0] : ''))
      .toUpperCase();
  }

  function scopeLine() {
    if (!Session.authEnabled) {
      return 'Accounts are switched off on this server — everyone signing in '
           + 'sees every role.';
    }
    if (Session.isAdmin) {
      return 'Recruiting team: every role, plus portal sync, reminder sends '
           + 'and accounts.';
    }
    const n = Session.roleCount;
    if (n === 0) {
      // The one genuinely confusing state, and it is not an error: the account
      // is fine, nobody has put them on a seat.
      return 'You are not listed as a hiring manager on any role yet, so there '
           + 'is nothing here to show. Ask the recruiting team to add you.';
    }
    return `Hiring manager: the ${n} role${n === 1 ? '' : 's'} you are listed `
         + 'on. Other roles are not shown.';
  }

  function render() {
    const bar = document.querySelector('.topbar-actions');
    if (!bar || !Session.authEnabled || !Session.user) return;
    if (document.getElementById('accountChip')) return;

    const user = Session.user;
    const wrap = document.createElement('div');
    wrap.className = 'account';
    wrap.id = 'accountChip';
    wrap.innerHTML = `
      <button class="account-btn" type="button" id="accountBtn"
              aria-haspopup="true" aria-expanded="false">
        <span class="account-initials" aria-hidden="true"></span>
        <span class="account-role"></span>
      </button>
      <div class="account-menu" id="accountMenu" hidden>
        <p class="account-email"></p>
        <p class="account-scope"></p>
        <div class="account-actions">
          <button class="btn btn-ghost" type="button" id="accountPwBtn">
            Change password
          </button>
          <button class="btn" type="button" id="accountOutBtn">Sign out</button>
        </div>
      </div>`;
    bar.appendChild(wrap);

    wrap.querySelector('.account-initials').textContent = initials(user);
    wrap.querySelector('.account-role').textContent =
      Session.isAdmin ? 'Recruiting' : 'Hiring manager';
    wrap.querySelector('.account-email').textContent = user.email;
    wrap.querySelector('.account-scope').textContent = scopeLine();

    const menu = document.getElementById('accountMenu');
    const button = document.getElementById('accountBtn');

    function toggle(open) {
      menu.hidden = !open;
      button.setAttribute('aria-expanded', String(open));
    }

    button.addEventListener('click', (event) => {
      event.stopPropagation();
      toggle(menu.hidden);
    });
    document.addEventListener('click', (event) => {
      if (!wrap.contains(event.target)) toggle(false);
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !menu.hidden) toggle(false);
    });

    document.getElementById('accountPwBtn').addEventListener('click', () => {
      location.href = '/login.html?change=1';
    });

    document.getElementById('accountOutBtn').addEventListener('click', () => {
      window.fetch('/api/auth/logout', { method: 'POST' })
        .catch(() => { /* leaving either way */ })
        .then(() => location.replace('/login.html'));
    });
  }

  // The chip is appended to a header that already exists in the HTML, so it
  // can be drawn as soon as /api/auth/me answers -- but not before the DOM is
  // parsed, if this ever gets moved into <head>.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      if (Session.user) render();
    });
  }
}());
