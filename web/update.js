/* Check for updates, in About (desktop app only). It runs only when the person presses Check for updates, or chooses
   Lastbook > Check for Updates… (the menu calls LBUpdate.fromMenu; no address starts it). engine/update.py does the check.
   On the Mac and Linux it only says whether a newer version is on the website. On Windows, the installed app also offers
   Update now (download and check the installer), then Restart and update (run it): each only when pressed. */
(() => {
  const E = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  let timer = null, menuAsked = false, later = false;
  const MB = n => (n / 1048576).toFixed(0);

  async function call(action) {
    const r = await fetch('/api/update/' + action, action === 'state' ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    return r.json();
  }

  function view(s) {
    const btn = (t, a = 'check') => `<button class="btn plain" data-upd="${a}">${t}</button>`;
    const go = (t, a) => `<button class="btn" data-upd="${a}">${t}</button>`;
    const ver = s.current ? `Version ${E(s.current)}.` : 'This copy has no version number (it was not built as an app).';
    switch (s.state) {
      case 'checking': return `${ver} Checking for updates…`;
      case 'up_to_date': return `${ver} Lastbook is up to date. ${btn('Check again')}`;
      case 'available':
        if (s.can_install && !later) return `${ver} Version ${E(s.version)} is available. Your library, local AI and chats stay. ${go('Update now', 'install')} ${btn('Later', 'later')}`;
        return `${ver} Version ${E(s.version)} is available. Download it from ${E(s.website)}. Your library, local AI and chats stay. ${btn('Check again')}`;
      case 'downloading': return `${ver} Downloading version ${E(s.version)}… ${s.total ? `${MB(s.done || 0)} of ${MB(s.total)} MB` : ''} ${btn('Cancel', 'cancel')}`;
      case 'verifying': return `${ver} Checking the download…`;
      case 'ready': return `${ver} Version ${E(s.version)} is ready.${later ? '' : ' Lastbook will close, update and open again. Your library, local AI and chats stay.'} ${go('Restart and update', 'restart')}${later ? '' : ' ' + btn('Later', 'later')}`;
      case 'restarting': return `${ver} Closing Lastbook to update…`;
      case 'not_published': return `${ver} No update information yet. ${btn('Try again')}`;
      case 'too_old_os': case 'error': return `${ver} ${E(s.message)} ${btn('Try again')}`;
      default: return `${ver} ${btn('Check for updates')}`;
    }
  }

  function paint(el, s) {
    el.innerHTML = view(s);
    clearTimeout(timer);
    if (['checking', 'downloading', 'verifying'].includes(s.state)) timer = setTimeout(() => refresh(el), 400);
  }

  async function refresh(el) {
    if (!el.isConnected) return;
    try { paint(el, await call('state')) } catch { el.textContent = 'Lastbook could not ask about updates.' }
  }

  async function check(el, action = 'check') {
    if (action === 'later') { later = true; return refresh(el) }
    if (action === 'check') later = false;
    try {
      const s = await call(action);
      if (s.error && !s.state) { el.textContent = s.error; return }
      paint(el, s);
    } catch { el.textContent = 'Lastbook could not ask about updates.' }
  }

  window.LBUpdate = {
    card: () => '<div class="card"><b>Lastbook app</b><small id="updBox">…</small></div>',
    mount(root) {
      const el = root.querySelector('#updBox'); if (!el) return;
      el.addEventListener('click', e => { const b = e.target.closest('[data-upd]'); if (b) check(el, b.dataset.upd) });
      if (menuAsked) { menuAsked = false; return check(el) }
      refresh(el);
    },
    fromMenu() {     // Lastbook > Check for Updates…: About, then one check
      const el = document.getElementById('updBox');
      if (el && location.hash.startsWith('#/about')) return check(el);
      menuAsked = true;
      location.hash = '#/about';
    },
  };
})();
