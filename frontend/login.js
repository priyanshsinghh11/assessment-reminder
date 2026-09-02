/*
 * Sign-in, and the first-time password change that follows it.
 *
 * This page is the only one in the frontend that is served without a session,
 * so it is deliberately the smallest: two forms, no shared state, and nothing
 * that reads or draws a candidate.
 *
 * It decides nothing about access. Every answer here comes from the server --
 * including where to go next, because "admins land on the reminders dashboard
 * and managers on evaluations" is a fact about permissions, and a copy of it
 * in the page would be a second place for it to be wrong.
 */

const $ = (id) => document.getElementById(id);

/* The CSRF token rides in a readable cookie; the session itself is HttpOnly
 * and is never touched from script. */
function cookie(name) {
  return document.cookie.split('; ')
    .filter((row) => row.startsWith(`${name}=`))
    .map((row) => decodeURIComponent(row.slice(name.length + 1)))[0] || '';
}

function csrfToken() {
  return cookie('ajaia_csrf');
}

async function post(path, body) {
  const resp = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken(),
    },
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try { data = await resp.json(); } catch { /* an error page, not JSON */ }
  if (!resp.ok) throw new Error(data.error || `Something went wrong (${resp.status}).`);
  return data;
}

function showError(el, message) {
  el.textContent = message;
  el.hidden = !message;
}

function setBusy(button, busy, label) {
  button.disabled = busy;
  button.textContent = busy ? 'Working…' : label;
}

/* --- showing what was typed -------------------------------------------
 *
 * A box that shows nothing is where a sign-in quietly goes wrong: the
 * password was right and the keyboard was not, and there is no way to see
 * that. Every password box gets its own button.
 *
 * The state lives on the button's aria-pressed rather than in a variable,
 * so what a screen reader announces and what the box is actually doing
 * cannot drift apart.
 */

const pwToggles = Array.from(document.querySelectorAll('.auth-pw-toggle'));

function setRevealed(button, revealed) {
  const input = $(button.dataset.reveals);
  input.type = revealed ? 'text' : 'password';
  button.textContent = revealed ? 'Hide' : 'Show';
  button.setAttribute('aria-pressed', String(revealed));
  button.setAttribute('aria-label', `${revealed ? 'Hide' : 'Show'} ${button.dataset.noun}`);
}

/* Nothing stays revealed across a change of form. The password carried over
 * from sign-in was typed on the other one, and a visible password should not
 * outlive the step somebody chose to show it on. */
function concealPasswords() {
  pwToggles.forEach((button) => setRevealed(button, false));
}

pwToggles.forEach((button) => {
  button.addEventListener('click', () => {
    setRevealed(button, button.getAttribute('aria-pressed') !== 'true');
    // Back to the box: the button is a detour in the middle of typing, not
    // the end of it.
    $(button.dataset.reveals).focus();
  });
});

/* Where to go once we are in. The server names it; `next` only overrides it
 * when the visitor was bounced off a page they were already heading for. */
function landing(result) {
  const params = new URLSearchParams(location.search);
  const next = params.get('next') || '';
  // Same-site paths only. An absolute URL here would make this page a
  // redirector for anyone who can get somebody to click a link to it.
  if (next.startsWith('/') && !next.startsWith('//')) return next;
  return result.home || '/';
}

/* --- signing in ------------------------------------------------------- */

const views = { signIn: $('signInView'), change: $('changeView') };

function show(which) {
  views.signIn.hidden = which !== 'signIn';
  views.change.hidden = which !== 'change';
  concealPasswords();
  const first = which === 'signIn' ? $('email') : $('currentPassword');
  first.focus();
}

$('signInForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  showError($('signInError'), '');
  setBusy($('signInBtn'), true, 'Sign in');

  const email = $('email').value.trim();
  const params = new URLSearchParams(location.search);

  try {
    const result = await post('/api/auth/login', {
      email,
      password: $('password').value,
      next: params.get('next') || '',
    });

    if (result.user && result.user.must_change) {
      // Carry the password across rather than asking for it again: they typed
      // it a second ago, and the change form needs it to prove they are the
      // person who did.
      $('changeEmail').value = email;
      $('currentPassword').value = $('password').value;
      $('password').value = '';
      show('change');
      setBusy($('signInBtn'), false, 'Sign in');
      return;
    }

    location.replace(landing(result));
  } catch (err) {
    showError($('signInError'), err.message);
    $('password').value = '';
    $('password').focus();
    setBusy($('signInBtn'), false, 'Sign in');
  }
});

/* --- setting a new password ------------------------------------------ */

$('changeForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  showError($('changeError'), '');

  const next = $('newPassword').value;
  if (next !== $('confirmPassword').value) {
    // Checked here because it is the one failure the server cannot see: it
    // only ever receives one of the two boxes.
    showError($('changeError'), 'The two new passwords do not match.');
    $('confirmPassword').focus();
    return;
  }

  setBusy($('changeBtn'), true, 'Save and continue');
  try {
    const result = await post('/api/auth/password', {
      current_password: $('currentPassword').value,
      new_password: next,
    });
    location.replace(landing(result));
  } catch (err) {
    showError($('changeError'), err.message);
    setBusy($('changeBtn'), false, 'Save and continue');
  }
});

$('signOutBtn').addEventListener('click', async () => {
  try { await post('/api/auth/logout'); } catch { /* leaving either way */ }
  location.replace('/login.html');
});

/* --- which form this page opened on ----------------------------------- */

(function start() {
  const params = new URLSearchParams(location.search);

  // ?change=1 is the redirect a signed-in account with a temporary password
  // gets from the server when it tries to open anything else.
  if (params.get('change')) {
    show('change');
    // They already have a session, so tell them whose it is -- a bare
    // "current password" box with no name on it invites the wrong one.
    fetch('/api/auth/me', { headers: { Accept: 'application/json' } })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data || !data.user) return;
        $('changeEmail').value = data.user.email;
        $('changeLede').textContent =
          `Signed in as ${data.user.email}, using a password somebody else `
          + 'chose. Pick your own before you carry on.';
      })
      .catch(() => { /* the form still works without the name */ });
    return;
  }

  show('signIn');
  if (params.get('next')) {
    $('signInLede').textContent =
      'That page needs you to be signed in. You will be taken back to it.';
  }
})();
