/*
 * The accounts panel: the people who can sign in, the roles each of them is
 * on, and the picker that assigns them. Admin only.
 *
 * Part of the evaluations dashboard, split out of a single 4,470-line
 * evaluations.js. These are classic scripts sharing one scope, loaded in
 * numbered order by evaluations.html -- the same scope and the same order the
 * one file had, so nothing about how they see each other changed.
 */

async function loadUsers() {
  try {
    const data = await api('/api/auth/users');
    state.users = data.users || [];
    renderUsers();
  } catch (err) {
    // A manager reaching this would be a bug, not a thing to shout about.
    if (!/recruiting-team account/i.test(err.message)) toast(err.message, true);
  }
}

/* --- opening and closing --------------------------------------------------
 *
 * The drawer sits over whatever is on screen and unloads none of it, so
 * closing puts you back on the same role, the same tab and the same scroll
 * position. That is the whole reason this moved out of the roles view: you
 * decide somebody should be on a role while you are looking at the role.
 */

function openAccounts() {
  $('accountsDrawer').hidden = false;
  toggleNewUserForm(false);
  // Opened fresh each time. A filter left over from the last visit hides
  // accounts that are still there, which reads as accounts that are gone.
  $('userSearch').value = '';
  if (state.users.length) renderUsers(); else loadUsers();
  $('userSearch').focus();
}

function closeAccounts() {
  closeRolePicker();
  $('accountsDrawer').hidden = true;
  $('accountsBtn')?.focus();
}

/* Three empty fields above the list of people you came here to edit are three
 * fields in the way, so the create form is folded until asked for. */
function toggleNewUserForm(open) {
  const form = $('newUserForm');
  const show = open === undefined ? form.hidden : Boolean(open);
  form.hidden = !show;
  const btn = $('newUserToggle');
  btn.setAttribute('aria-expanded', String(show));
  btn.textContent = show ? 'Cancel' : 'New account';
  if (show) $('newUserEmail').focus();
}

/* --- the list ------------------------------------------------------------ */

/* The search matches role titles as well as people, because the question asked
 * of this screen is at least as often "who is on Chief of Staff?" as it is
 * "what can Anita see?". Every word has to match something; two words narrow
 * rather than widen. */
function matchesUserSearch(user, needle) {
  if (!needle) return true;
  const hay = [user.email, user.name,
    user.is_admin ? 'recruiting admin' : 'hiring manager']
    .concat((user.roles || []).map((r) => r.title))
    .join(' ').toLowerCase();
  return needle.split(/\s+/).every((word) => hay.includes(word));
}

// plural() is the one further down this file, beside the shortlist counts.

function userInitials(user) {
  const parts = String(user.name || user.email || '').split(/[\s.@_-]+/)
    .filter(Boolean);
  return ((parts[0] || '?')[0] + (parts[1] ? parts[1][0] : '')).toUpperCase();
}

function renderUsers() {
  const all = state.users;
  const needle = $('userSearch').value.trim().toLowerCase();
  // Recruiting first, then managers, each A-Z. The admin list is the short one
  // and the one somebody scanning for "who can see everything" wants on top.
  const rows = all.filter((user) => matchesUserSearch(user, needle))
    .slice()
    .sort((a, b) => (Number(b.is_admin) - Number(a.is_admin))
      || a.email.localeCompare(b.email));

  const admins = all.filter((user) => user.is_admin).length;
  $('accountsCount').textContent = all.length
    ? `${plural(all.length, 'account')} · ${admins} recruiting · `
      + `${plural(all.length - admins, 'hiring manager')}`
    : 'Nobody has an account yet.';

  const empty = $('usersEmpty');
  empty.hidden = rows.length > 0;
  // Two different nothings: an empty server, and a search that found nobody.
  empty.textContent = all.length
    ? 'No account matches that search.' : 'No accounts yet.';

  $('usersBody').innerHTML = rows.map(userCard).join('');

  for (const btn of $('usersBody').querySelectorAll('[data-reset]')) {
    btn.addEventListener('click', () => resetUserPassword(btn.dataset.reset));
  }
  for (const btn of $('usersBody').querySelectorAll('[data-role]')) {
    btn.addEventListener('click', () => {
      // The half of the switch that is already on. Clicking it should be the
      // no-op it looks like, not a confirm dialog offering the state you are
      // already in.
      if (btn.classList.contains('is-on')) return;
      setUserRole(btn.dataset.role, btn.dataset.to);
    });
  }
  for (const btn of $('usersBody').querySelectorAll('[data-del]')) {
    btn.addEventListener('click', () => removeUser(btn.dataset.del));
  }
  for (const btn of $('usersBody').querySelectorAll('[data-unassign]')) {
    btn.addEventListener('click', () =>
      unassignRole(btn.dataset.unassign, Number(btn.dataset.job)));
  }
  for (const input of $('usersBody').querySelectorAll('.role-picker-input')) {
    input.addEventListener('focus', () => openRolePicker(input));
    input.addEventListener('input', () => openRolePicker(input));
    input.addEventListener('keydown', (event) => rolePickerKey(event, input));
    input.addEventListener('blur', () => closeRolePicker());
  }

  restorePickerFocus();
}

function userCard(user) {
  const flags = [];
  if (!user.active) flags.push('<span class="flag">disabled</span>');
  if (user.must_change) flags.push('<span class="flag">password not set</span>');
  const meta = [
    esc(user.name) || '<span class="flag">no name</span>',
    `last signed in ${user.last_login_at
      ? esc(shortDate(user.last_login_at)) : 'never'}`,
  ].concat(flags).join(' · ');

  return `
    <article class="user-card">
      <div class="user-card-head">
        <span class="user-avatar" aria-hidden="true">${esc(userInitials(user))}</span>
        <div class="user-id">
          <div class="user-email">${esc(user.email)}</div>
          <div class="user-meta">${meta}</div>
        </div>
        <!-- Access as a two-way switch. The old button named where you would
             end up and the cell beside it named where you were, so telling a
             recruiter from a manager meant reading both. -->
        <div class="access-toggle" role="group"
             aria-label="Access for ${esc(user.email)}">
          <button type="button" class="${user.is_admin ? '' : 'is-on'}"
                  data-role="${esc(user.email)}" data-to="manager"
                  ${user.is_admin ? '' : 'aria-current="true"'}
                  title="Sees only the roles listed on this card">Hiring manager</button>
          <button type="button" class="${user.is_admin ? 'is-on' : ''}"
                  data-role="${esc(user.email)}" data-to="admin"
                  ${user.is_admin ? 'aria-current="true"' : ''}
                  title="Sees every role and every button on these pages">Recruiting</button>
        </div>
        <div class="user-actions">
          <button class="btn btn-ghost" data-reset="${esc(user.email)}"
                  title="Set a new temporary password and sign them out">
            Reset password</button>
          <button class="btn btn-ghost btn-danger" data-del="${esc(user.email)}"
                  title="Take away their sign-in">Remove</button>
        </div>
      </div>
      <div class="user-card-roles">${roleCell(user)}</div>
    </article>`;
}

/* The roles an account is on, and the two controls that change it.
 *
 * Writes the SAME list as the Hiring managers editor on the role itself --
 * `hiring_managers` -- rather than a permission of its own. Two doors onto one
 * list; there is no second answer to who owns a seat. Which also means every
 * change here changes who a shortlist is emailed to, so the copy says so.
 */
function roleCell(user) {
  const on = user.roles || [];
  const onIds = new Set(on.map((r) => r.id));
  const who = esc(user.name || user.email);
  const chips = on.map((r) => `
    <span class="role-chip">${esc(r.title)}<button type="button"
      data-unassign="${esc(user.email)}" data-job="${r.id}"
      title="Take ${who} off ${esc(r.title)}"
      aria-label="Remove from ${esc(r.title)}">&times;</button></span>`).join('');

  // An admin sees every role whatever this says, so for them the list means
  // something different -- it is who gets the shortlist email, not who can
  // look. Saying "Every role" and then offering to add one would read as a
  // contradiction, so the sentence above the chips says which it is.
  const note = user.is_admin
    ? '<p class="dim">Every role, as an admin. What is listed here only '
      + 'decides which shortlists are emailed to them.</p>'
    : on.length ? '' : '<p class="dim">Not on any role yet — there is nothing '
      + 'for them to open until one is added.</p>';

  // Only roles they are not already on. An option that does nothing is an
  // option somebody picks twice wondering why nothing happened.
  const left = state.roles.filter((r) => !onIds.has(r.id)).length;

  return `${note}<div class="role-chips">${chips}</div>
    <div class="role-picker" data-picker="${esc(user.email)}">
      <input type="text" class="role-picker-input" autocomplete="off"
             role="combobox" aria-expanded="false" aria-autocomplete="list"
             aria-label="Add ${esc(user.email)} to a role"
             placeholder="${left
               ? 'Add to a role — type to filter' : 'On every role already'}"
             ${left ? '' : 'disabled'}>
      <div class="role-picker-menu" hidden></div>
    </div>`;
}

/* --- the role picker ------------------------------------------------------
 *
 * A <select> of forty roles is forty roles to scroll past, and the recruiter
 * already knows the title they are looking for. This filters as they type and
 * hands focus back after a pick, so putting somebody on three roles is three
 * words rather than three trips through the same list.
 */

function openRolePicker(input) {
  const picker = input.closest('.role-picker');
  const menu = picker.querySelector('.role-picker-menu');
  const user = state.users.find((u) => u.email === picker.dataset.picker);
  const onIds = new Set(((user && user.roles) || []).map((r) => r.id));
  const needle = input.value.trim().toLowerCase();

  const options = state.roles
    .filter((r) => !onIds.has(r.id))
    .filter((r) => !needle || r.title.toLowerCase().includes(needle));

  menu.innerHTML = options.length
    ? options.map((r, i) => `<button type="button" data-job="${r.id}"
        class="${i === 0 ? 'is-active' : ''}">${esc(r.title)}</button>`).join('')
    : '<p class="role-picker-none">No role matches that.</p>';
  menu.hidden = false;
  input.setAttribute('aria-expanded', 'true');

  // mousedown rather than click: a click fires after the input has blurred,
  // which has already closed the menu out from under the pointer. Preventing
  // the default also keeps the caret where it was.
  for (const btn of menu.querySelectorAll('[data-job]')) {
    btn.addEventListener('mousedown', (event) => {
      event.preventDefault();
      pickRole(picker.dataset.picker, Number(btn.dataset.job));
    });
  }
}

function closeRolePicker() {
  for (const menu of document.querySelectorAll('.role-picker-menu')) {
    menu.hidden = true;
  }
  for (const input of document.querySelectorAll('.role-picker-input')) {
    input.setAttribute('aria-expanded', 'false');
  }
}

function rolePickerKey(event, input) {
  const picker = input.closest('.role-picker');
  const menu = picker.querySelector('.role-picker-menu');

  if (event.key === 'Escape') {
    // Swallowed only while the menu is up, so it shuts the menu and nothing
    // else -- otherwise the page-level handler would close the whole drawer on
    // the same keystroke. With no menu open there is nothing here to close and
    // Escape should mean what it means everywhere else on the page.
    if (menu.hidden) return;
    event.stopPropagation();
    closeRolePicker();
    return;
  }
  if (menu.hidden && (event.key === 'ArrowDown' || event.key === 'Enter')) {
    openRolePicker(input);
  }

  const options = Array.from(menu.querySelectorAll('[data-job]'));
  if (!options.length) return;
  const at = Math.max(0,
    options.findIndex((btn) => btn.classList.contains('is-active')));

  if (event.key === 'Enter') {
    event.preventDefault();
    pickRole(picker.dataset.picker, Number(options[at].dataset.job));
    return;
  }
  if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
  event.preventDefault();
  const to = (at + (event.key === 'ArrowDown' ? 1 : options.length - 1))
    % options.length;
  options[at].classList.remove('is-active');
  options[to].classList.add('is-active');
  options[to].scrollIntoView({ block: 'nearest' });
}

/* Adding asks nothing: it is visible on the card the moment it lands, and the
 * x beside it is the undo. Dropping still asks -- see unassignRole() -- because
 * it takes a role away from somebody who may be halfway through reviewing it,
 * and takes them off its shortlist mail with it. */
function pickRole(email, jobId) {
  state.focusPicker = email;
  closeRolePicker();
  assignRole(email, jobId);
}

function restorePickerFocus() {
  const email = state.focusPicker;
  state.focusPicker = null;
  if (!email) return;
  for (const picker of $('usersBody').querySelectorAll('.role-picker')) {
    if (picker.dataset.picker !== email) continue;
    const input = picker.querySelector('.role-picker-input');
    if (input && !input.disabled) input.focus();
    return;
  }
}

async function setUserRoles(email, jobIds, verb) {
  try {
    const data = await api(`/api/auth/users/${encodeURIComponent(email)}/roles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_ids: jobIds }),
    });
    state.users = data.users || state.users;
    renderUsers();
    toast(data.message);
    // The role cards carry an owner chip and the "nobody to send to" warning,
    // so the grid above this panel is now out of date.
    if (data.stale_roles) loadRoles();
  } catch (err) {
    toast(err.message, true);
  }
}

function currentRoleIds(email) {
  const user = state.users.find((u) => u.email === email);
  return (user ? user.roles || [] : []).map((r) => r.id);
}

function assignRole(email, jobId) {
  setUserRoles(email, currentRoleIds(email).concat([jobId]), 'added');
}

function unassignRole(email, jobId) {
  const role = state.roles.find((r) => r.id === jobId);
  if (!window.confirm(
    `Take ${email} off ${role ? role.title : `role ${jobId}`}?

`
    + 'They lose access to it immediately, and its shortlist will no longer be '
    + 'emailed to them.')) return;
  setUserRoles(email, currentRoleIds(email).filter((id) => id !== jobId),
    'removed');
}

/* A password is on screen once. Rendered into the page rather than a toast,
 * which disappears on its own after four seconds. */
function showSecret(who, password) {
  if (!password) return;
  $('secretWho').textContent = who;
  $('secretPassword').textContent = password;
  $('newUserSecret').hidden = false;
  $('newUserSecret').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

async function createUser(event) {
  event.preventDefault();
  const email = $('newUserEmail').value.trim().toLowerCase();
  if (!email.includes('@')) {
    toast('That is not an email address.', true);
    return;
  }
  const btn = $('newUserBtn');
  btn.disabled = true;
  try {
    const data = await api('/api/auth/users', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email,
        name: $('newUserName').value.trim(),
        role: $('newUserRole').value,
      }),
    });
    state.users = data.users || state.users;
    renderUsers();
    $('newUserForm').reset();
    toggleNewUserForm(false);
    showSecret(`Password for ${email}`, data.password);
    toast(data.message);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function resetUserPassword(email) {
  if (!window.confirm(
    `Reset the password for ${email}?\n\n`
    + 'They will be signed out everywhere and asked to set a new one. '
    + 'The temporary password is shown once.')) return;
  try {
    const data = await api(`/api/auth/users/${encodeURIComponent(email)}/password`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: '{}' });
    state.users = data.users || state.users;
    renderUsers();
    showSecret(`New password for ${email}`, data.password);
    toast(data.message);
  } catch (err) {
    toast(err.message, true);
  }
}

async function setUserRole(email, role) {
  const what = role === 'admin'
    ? `Give ${email} a recruiting account?\n\nThey will see every role, every `
      + 'candidate, and the buttons that email them.'
    : `Make ${email} a hiring manager?\n\nThey will see only the roles their `
      + 'address is listed on, and will be signed out now.';
  if (!window.confirm(what)) return;
  try {
    const data = await api(`/api/auth/users/${encodeURIComponent(email)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role }),
    });
    state.users = data.users || state.users;
    renderUsers();
    toast(data.message);
  } catch (err) {
    toast(err.message, true);
  }
}

async function removeUser(email) {
  if (!window.confirm(
    `Remove the account for ${email}?\n\n`
    + 'They lose access immediately. This does not take them off any role — '
    + 'the shortlist emails still go to them.')) return;
  try {
    const data = await api(`/api/auth/users/${encodeURIComponent(email)}`,
      { method: 'DELETE' });
    state.users = data.users || state.users;
    renderUsers();
    toast(data.message);
  } catch (err) {
    toast(err.message, true);
  }
}
