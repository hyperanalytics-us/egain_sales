/* Router, dataset switching and weblog upload. */

const App = (() => {
  const h = UI.h, esc = UI.esc, num = UI.num;

  const ICON = {
    dashboard: '<path d="M3 3h7v8H3zM14 3h7v5h-7zM14 11h7v10h-7zM3 14h7v7H3z"/>',
    industry: '<path d="M3 21V9l6 4V9l6 4V4h6v17z"/>',
    product: '<path d="M12 2 3 7v10l9 5 9-5V7z"/><path d="M3 7l9 5 9-5M12 12v10"/>',
    campaign: '<path d="M3 11v2a1 1 0 0 0 1 1h3l6 5V5L7 10H4a1 1 0 0 0-1 1z"/><path d="M17 8a5 5 0 0 1 0 8"/>',
    recommendations: '<path d="M9 21h6M10 17h4M12 3a6 6 0 0 0-3 11v1h6v-1a6 6 0 0 0-3-11z"/>',
    ai: '<path d="M12 3l2 5 5 2-5 2-2 5-2-5-5-2 5-2z"/>',
    sources: '<path d="M12 3v18M3 12h18M5 5l14 14M19 5L5 19"/>',
    ip: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18z"/>',
  };

  const ROUTES = [
    { id: 'dashboard', title: 'Dashboard', icon: 'dashboard', render: Pages.dashboard, group: 'Overview' },
    { id: 'industry', title: 'Industry Prospects', icon: 'industry', render: Pages.industry, group: 'Prospects' },
    { id: 'product', title: 'Product Prospects', icon: 'product', render: Pages.product },
    { id: 'campaign', title: 'Campaign Prospects', icon: 'campaign', render: Pages.campaign },
    { id: 'recommendations', title: 'Recommendations', icon: 'recommendations', render: Pages.recommendations, group: 'Action' },
    { id: 'ask', title: 'ASK AI', icon: 'ai', render: Pages.askAI },
    { id: 'sources', title: 'Top Sources', icon: 'sources', render: Pages.sources, group: 'Traffic' },
    { id: 'ip', title: 'IP Analysis', icon: 'ip', render: Pages.ipAnalysis },
  ];

  const state = {
    route: 'dashboard',
    params: {},
    datasets: [],
    aiHistory: [],
    aiSession: 's' + Math.random().toString(36).slice(2, 9),
  };

  function buildNav() {
    const nav = document.getElementById('nav');
    nav.innerHTML = ROUTES.map((r) => `
      ${r.group ? `<div class="group">${esc(r.group)}</div>` : ''}
      <a href="#${r.id}" data-r="${r.id}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICON[r.icon]}</svg>
        <span>${esc(r.title)}</span>
      </a>`).join('');
  }

  function syncNav() {
    document.querySelectorAll('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.r === state.route));
    const r = ROUTES.find((x) => x.id === state.route);
    document.getElementById('page-title').textContent = r ? r.title : 'ESP';
  }

  async function render() {
    const view = document.getElementById('view');
    view.innerHTML = '';
    syncNav();
    if (!API.ds) {
      view.appendChild(h(`<div class="empty">
        <p><b>No weblog loaded yet.</b></p>
        <p class="muted">Upload a website visitor log to start analysing prospects.</p>
        <p><button class="btn primary" onclick="document.getElementById('btn-upload').click()">Upload weblog</button></p>
      </div>`));
      return;
    }
    const route = ROUTES.find((r) => r.id === state.route) || ROUTES[0];
    try {
      await route.render(view, state.params);
    } catch (e) {
      view.innerHTML = '';
      view.appendChild(Pages.errorBox(e));
    }
  }

  function go(routeId, params) {
    if (!ROUTES.some((r) => r.id === routeId)) return;
    state.params = params || {};
    if (state.route === routeId) {
      render();                       // same page, new filters
    } else {
      location.hash = '#' + routeId;  // hashchange drives the single render
    }
  }

  function onHash() {
    const id = (location.hash || '#dashboard').slice(1);
    const route = ROUTES.find((r) => r.id === id);
    state.route = route ? route.id : 'dashboard';
    render();
  }

  /* --------------------------------------------------------- datasets ---- */
  async function loadDatasets(preferId) {
    const cat = await API.datasets();
    state.datasets = cat.datasets;
    const sel = document.getElementById('ds-select');
    const ready = cat.datasets.filter((d) => d.status === 'ready');
    sel.innerHTML = ready.map((d) => `<option value="${esc(d.id)}">${esc(d.name)}${d.id === cat.default_id ? ' (default)' : ''}</option>`).join('')
      || '<option value="">— none —</option>';
    const pick = (preferId && ready.some((d) => d.id === preferId)) ? preferId
      : (ready.some((d) => d.id === cat.default_id) ? cat.default_id : (ready[0] && ready[0].id));
    API.ds = pick || null;
    if (pick) sel.value = pick;
    const cur = ready.find((d) => d.id === pick);
    document.getElementById('ds-foot').innerHTML = cur
      ? `<b>${esc(cur.name)}</b><br>${num(cur.total_requests || 0)} requests · ${num(cur.unique_ips || 0)} IPs`
      : 'No weblog loaded';
    return cat;
  }

  function uploadDialog() {
    const body = h(`<div>
      <p class="muted small">The log layout is fixed: <code>IP, Domain, Date &amp; Time (UTC), Request Type,
      Page URL, Referral URL, User Agent</code>. .xlsx and .csv are both accepted, up to
      <b><span data-max>50</span> MB</b>.</p>
      <label class="field" style="display:block;margin-bottom:12px">
        <span>Name this uploaded file</span>
        <input type="text" data-name placeholder="e.g. February 2025 web log" style="width:100%">
      </label>
      <div class="drop" data-drop>Click to choose a weblog, or drop it here</div>
      <label style="display:block;margin-top:12px"><input type="checkbox" data-default checked> Make this the default file</label>
      <input type="file" accept=".xlsx,.xlsm,.csv,.tsv,.txt,.log" style="display:none" data-file>
      <div data-out style="margin-top:14px"></div>
    </div>`);
    const { close } = UI.modal('Upload weblog', body);
    API.datasets().then((c) => { body.querySelector('[data-max]').textContent = c.max_upload_mb; });

    const file = body.querySelector('[data-file]');
    const drop = body.querySelector('[data-drop]');
    const out = body.querySelector('[data-out]');
    const nameInput = body.querySelector('[data-name]');
    drop.onclick = () => file.click();
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('over'); };
    drop.ondragleave = () => drop.classList.remove('over');
    drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove('over'); if (e.dataTransfer.files[0]) start(e.dataTransfer.files[0]); };
    file.onchange = () => file.files[0] && start(file.files[0]);

    async function start(f) {
      if (!nameInput.value.trim()) nameInput.value = f.name.replace(/\.[^.]+$/, '');
      out.innerHTML = '<div class="progress"><span style="width:4%"></span></div><div class="small muted" data-msg>Uploading…</div>';
      const bar = out.querySelector('.progress span');
      const msg = out.querySelector('[data-msg]');
      const fd = new FormData();
      fd.append('file', f);
      fd.append('name', nameInput.value.trim());
      fd.append('make_default', body.querySelector('[data-default]').checked ? 'true' : 'false');
      let job;
      try {
        job = await API.form('/api/datasets/upload', fd);
      } catch (e) {
        out.innerHTML = `<div class="note err">${esc(e.message)}</div>`;
        return;
      }
      const poll = setInterval(async () => {
        try {
          const s = await API.job(job.job_id);
          bar.style.width = Math.max(4, s.percent || 0) + '%';
          msg.textContent = s.message || '';
          if (s.status === 'done') {
            clearInterval(poll);
            msg.innerHTML = `Analysed <b>${num(s.meta.total_requests)}</b> requests from
              <b>${num(s.meta.unique_ips)}</b> IPs in ${s.meta.ingest_seconds}s —
              <b>${num(s.meta.eligible_ips)}</b> eligible prospects.`;
            await loadDatasets(s.dataset_id);
            setTimeout(() => { close(); render(); UI.toast('Weblog analysed and loaded.'); }, 1200);
          } else if (s.status === 'error') {
            clearInterval(poll);
            out.innerHTML = `<div class="note err">${esc(s.message)}</div>`;
          }
        } catch (e) { clearInterval(poll); out.innerHTML = `<div class="note err">${esc(e.message)}</div>`; }
      }, 700);
    }
  }

  function manageDialog() {
    const body = h('<div></div>');
    body.appendChild(Pages.loading());
    const { close } = UI.modal('Uploaded files', body, [], { wide: true });
    API.datasets().then((cat) => {
      body.innerHTML = '';
      if (!cat.datasets.length) { body.appendChild(h('<div class="empty">No weblogs uploaded yet.</div>')); return; }
      const list = h('<div></div>');
      cat.datasets.forEach((d) => {
        const row = h(`<div class="play" style="display:flex;gap:14px;align-items:center">
          <div style="flex:1">
            <b>${esc(d.name)}</b> ${d.id === cat.default_id ? '<span class="pill t-a-high">default</span>' : ''}
            ${d.status !== 'ready' ? `<span class="pill r-medium">${esc(d.status)}</span>` : ''}
            <div class="small muted">${esc(d.original_filename || '')} ·
              ${num(d.total_requests || 0)} requests · ${num(d.unique_ips || 0)} IPs ·
              ${num(d.eligible_ips || 0)} prospects · ${(d.file_size / 1048576).toFixed(1)} MB</div>
          </div>
          <button class="btn sm" data-use>Use</button>
          <button class="btn sm" data-def>Make default</button>
          <button class="btn sm" data-del>Delete</button>
        </div>`);
        row.querySelector('[data-use]').onclick = async () => { await loadDatasets(d.id); close(); render(); };
        row.querySelector('[data-def]').onclick = async () => {
          await API.post(`/api/datasets/${d.id}/default`); await loadDatasets(d.id); close(); render();
          UI.toast(`"${d.name}" is now the default file.`);
        };
        row.querySelector('[data-del]').onclick = async () => {
          if (!confirm(`Delete "${d.name}" and its analysis?`)) return;
          await API.del(`/api/datasets/${d.id}`); await loadDatasets(); close(); render();
        };
        list.appendChild(row);
      });
      body.appendChild(list);
    }).catch((e) => { body.innerHTML = ''; body.appendChild(Pages.errorBox(e)); });
  }

  /* ------------------------------------------------------------- login --- */
  let loginOpen = false;

  function showLogin(message) {
    if (loginOpen) return;
    loginOpen = true;
    const root = document.getElementById('overlay-root');
    const el = h(`<div class="overlay" data-login>
      <div class="modal" style="width:min(400px,100%)">
        <h2>Sign in to ESP</h2>
        <div class="body">
          <p class="muted small" style="margin-top:0">This workspace holds prospect data. Enter the shared password to continue.</p>
          <input type="password" data-pw placeholder="Password" style="width:100%" autocomplete="current-password">
          <div data-msg class="note err small" style="margin:12px 0 0;display:none"></div>
        </div>
        <div class="foot"><button class="btn primary" data-go>Sign in</button></div>
      </div></div>`);
    root.appendChild(el);
    const pw = el.querySelector('[data-pw]');
    const msg = el.querySelector('[data-msg]');
    if (message) { msg.textContent = message; msg.style.display = ''; }
    const submit = async () => {
      const btn = el.querySelector('[data-go]');
      btn.disabled = true;
      try {
        await API.login(pw.value);
        el.remove(); loginOpen = false;
        await loadDatasets(); render();
      } catch (e) {
        msg.textContent = e.message || 'Incorrect password.';
        msg.style.display = '';
        pw.select();
      } finally { btn.disabled = false; }
    };
    el.querySelector('[data-go]').onclick = submit;
    pw.onkeydown = (e) => { if (e.key === 'Enter') submit(); };
    pw.focus();
  }

  async function boot() {
    buildNav();
    API.onAuthRequired = () => showLogin('Your session expired. Sign in again.');
    let authed = true;
    try {
      const st = await API.authStatus();
      authed = st.signed_in;
      if (st.auth_required) {
        const out = h('<button class="btn sm" style="margin-top:8px">Sign out</button>');
        out.onclick = async () => { await API.logout(); location.reload(); };
        document.querySelector('.side-foot').appendChild(out);
      }
      if (!authed) { showLogin(); return; }
    } catch (e) { /* auth endpoint unreachable - fall through to the normal error path */ }
    document.getElementById('btn-upload').onclick = uploadDialog;
    document.getElementById('btn-manage').onclick = manageDialog;
    document.getElementById('ds-select').onchange = async (e) => {
      await loadDatasets(e.target.value);
      state.aiHistory = [];
      render();
    };
    window.addEventListener('hashchange', onHash);
    try { await loadDatasets(); } catch (e) { UI.toast(e.message, 'err'); }
    onHash();
  }

  const api = { go, render, boot, ROUTES };
  Object.defineProperty(api, 'aiHistory', { get: () => state.aiHistory, set: (v) => { state.aiHistory = v; } });
  Object.defineProperty(api, 'aiSession', { get: () => state.aiSession, set: (v) => { state.aiSession = v; } });
  return api;
})();

document.addEventListener('DOMContentLoaded', App.boot);
