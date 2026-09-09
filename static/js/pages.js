/* Page renderers.  Each returns a DocumentFragment/Element for #view. */

const Pages = (() => {
  const h = UI.h, esc = UI.esc, num = UI.num;

  const PROSPECT_HEADERS = {
    ip: 'IP', uid: 'CRM UID', company: 'Company / Account', domain: 'Domain',
    a_immediate: 'A-Immediate', a_high: 'A-High', b_warm: 'B-Warm', c_nurture: 'C-Nurture',
    avg_score: 'Avg score', named_accounts: 'Named accounts', intent_ips: 'Prospect IPs',
    ref_host: 'Referrer host', unique_ips: 'Unique IPs', top_intent_pages: 'Top intent pages',
    why: 'Why sales should care', next_action: 'Next action', score_components: 'Score components',
    key: 'Name', ips: 'IPs', uids: 'CRM UIDs', identified: 'Named contacts',
    account: 'Account', prospect_ips: 'Prospect IPs', contacts: 'Known people',
    best_ip: 'Top address', page_views: 'Page views',
    best_tier: 'Best tier', best_score: 'Best score', reach: 'Reach',
    contact: 'Contact / Lead', contact_company: 'Contact company',
  };

  const DEFAULT_PROSPECT_COLS = [
    'rank', 'tier', 'score', 'ip', 'company', 'contact', 'domain', 'source', 'uid', 'campaign',
    'products', 'industries', 'demo_views', 'contact_views', 'page_views', 'sessions',
    'active_days', 'first_visit', 'last_visit', 'crawler_risk', 'why', 'next_action',
  ];

  function loading(msg = 'Loading…') { return h(`<div class="empty"><span class="spinner"></span> ${esc(msg)}</div>`); }

  function errorBox(e) { return h(`<div class="note err">${esc(e.message || String(e))}</div>`); }

  function kpi(label, value, hint, cls = '') {
    return `<div class="kpi ${cls}"><div class="label">${esc(label)}</div>
      <div class="value">${typeof value === 'number' ? num(value) : esc(value)}</div>
      ${hint ? `<div class="hint">${esc(hint)}</div>` : ''}</div>`;
  }

  /* ----------------------------------------------------- IP detail modal -- */
  async function showIp(ip) {
    const body = h('<div></div>');
    body.appendChild(loading());
    const { close } = UI.modal(`Visitor ${ip}`, body, [], { wide: true });
    try {
      const d = await API.ipDetail(ip);
      body.innerHTML = '';
      const r = d.raw;
      body.appendChild(h(`<div class="kpis">
        ${kpi('Intent score', r.score, r.tier)}
        ${kpi('Page views', r.page_views, `${num(r.unique_pages)} unique pages`)}
        ${kpi('Sessions', r.sessions, `${num(r.active_days)} active day(s)`)}
        ${kpi('Contact / Demo', `${num(r.contact_views)} / ${num(r.demo_views)}`, 'conversion page views')}
        ${kpi('Crawler risk', r.crawler_risk, r.risk_reason || 'no automation signals')}
        ${kpi('Source', r.source, r.top_referrer || 'no referrer recorded')}
      </div>`));
      body.appendChild(h(`<div class="card" style="margin-bottom:16px"><div class="body">
        <div class="defs">
          <div><span class="k">Company / account</span><span>${esc(d.company || '— (upload an IP mapping to resolve)')}</span></div>
          <div><span class="k">Domain</span><span>${esc(d.domain || '—')}</span></div>
          <div><span class="k">CRM UID</span><span class="small" style="font-family:var(--mono)">${esc(r.uid || '—')}</span></div>
          <div><span class="k">Campaigns</span><span>${esc(r.campaigns || '—')}</span></div>
          <div><span class="k">Products of interest</span><span>${esc(r.products || '—')}</span></div>
          <div><span class="k">Industries researched</span><span>${esc(r.industries || '—')}</span></div>
          <div><span class="k">First / last visit</span><span>${esc(d.first_visit)} → ${esc(d.last_visit)} UTC</span></div>
          <div><span class="k">Why sales should care</span><span>${esc(r.why || '—')}</span></div>
          <div><span class="k">Score components</span><span>${esc(r.score_components)}</span></div>
          <div><span class="k">Next action</span><span>${esc(r.next_action)}</span></div>
          <div><span class="k">User agent</span><span class="small">${esc((d.user_agents[0] || {}).ua || '—')}</span></div>
        </div></div></div>`));
      const pagesCard = h('<div class="card" style="margin-bottom:16px"><h3>Pages viewed</h3><div class="body tight"></div></div>');
      pagesCard.querySelector('.body').appendChild(
        UI.table(['path', 'category', 'product', 'industry', 'views', 'first_visit', 'last_visit'], d.pages,
          { sortCol: 'views', sortDir: -1 }));
      body.appendChild(pagesCard);
      const tl = h('<div class="card"><h3>Request timeline (first 300)</h3><div class="body tight"></div></div>');
      tl.querySelector('.body').appendChild(
        UI.table(['when', 'path', 'category', 'campaign', 'uid', 'ref_host'], d.timeline, {}));
      body.appendChild(tl);
    } catch (e) { body.innerHTML = ''; body.appendChild(errorBox(e)); }
  }

  /* -------------------------------------------------- prospect table pane - */
  function prospectPane(baseFilters = {}, opts = {}) {
    const state = { limit: 100, offset: 0, ...baseFilters };
    const root = h(`<div class="card">
      <h3>${esc(opts.title || 'Prospects')}<span class="spacer"></span>
        <span class="small muted" data-count></span>
        <a class="btn sm" data-export>Export CSV</a>
      </h3>
      <div class="body"><div class="filters"></div></div>
      <div data-rows></div>
      <div class="body" data-pager style="display:flex;gap:10px;align-items:center"></div>
    </div>`);
    const filters = root.querySelector('.filters');
    const rowsPane = root.querySelector('[data-rows]');
    const pager = root.querySelector('[data-pager]');

    const mk = (label, node) => { const l = h(`<label class="field"><span>${esc(label)}</span></label>`); l.appendChild(node); return l; };
    const sel = (name, options, value) => {
      const s = h(`<select data-f="${name}"></select>`);
      options.forEach(([v, t]) => s.appendChild(h(`<option value="${esc(v)}" ${v === value ? 'selected' : ''}>${esc(t)}</option>`)));
      return s;
    };

    if (!opts.hideTier) filters.appendChild(mk('Tier', sel('tier', [['', 'All tiers'], ['A - Immediate', 'A - Immediate'], ['A - High', 'A - High'], ['B - Warm', 'B - Warm'], ['C - Nurture', 'C - Nurture']], state.tier || '')));
    filters.appendChild(mk('Source', sel('source', [['', 'All sources'], ['Email / Marketing', 'Email / Marketing'], ['Organic Search', 'Organic Search'], ['LinkedIn', 'LinkedIn'], ['AI Assistant', 'AI Assistant'], ['Social', 'Social'], ['Other Referral', 'Other Referral'], ['Internal (egain.com)', 'Internal'], ['Direct / Unknown', 'Direct / Unknown']], state.source || '')));
    filters.appendChild(mk('Crawler risk', sel('risk', [['', 'Any'], ['Low', 'Low only'], ['Medium', 'Medium'], ['High', 'High']], state.risk || '')));
    filters.appendChild(mk('Min score', h(`<input type="number" data-f="min_score" min="0" max="100" step="5" style="width:92px" value="${state.min_score || ''}">`)));
    filters.appendChild(mk('Rows', sel('limit', [['50', '50'], ['100', '100'], ['250', '250'], ['500', '500'], ['1000', '1000']], String(state.limit))));
    filters.appendChild(mk('Search', h(`<input type="search" data-f="search" placeholder="IP, company, product…" style="width:200px" value="${esc(state.search || '')}">`)));
    const namedBox = h(`<label class="field"><span>Named only</span><span style="padding:7px 0"><input type="checkbox" data-f="named_only"> accounts</span></label>`);
    filters.appendChild(namedBox);

    let timer = null;
    filters.addEventListener('input', (e) => {
      const f = e.target.dataset.f; if (!f) return;
      state[f] = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
      if (f === 'limit') state.limit = +e.target.value;
      state.offset = 0;
      clearTimeout(timer); timer = setTimeout(load, e.target.type === 'search' ? 320 : 0);
    });

    async function load() {
      rowsPane.innerHTML = ''; rowsPane.appendChild(loading());
      try {
        const data = await API.prospects(state);
        rowsPane.innerHTML = '';
        const cols = opts.columns || DEFAULT_PROSPECT_COLS;
        rowsPane.appendChild(UI.table(cols, data.rows, { headers: PROSPECT_HEADERS, onRow: (r) => showIp(r.ip) }));
        root.querySelector('[data-count]').textContent =
          `${num(data.total)} matching prospects — showing ${num(data.offset + 1)}–${num(data.offset + data.rows.length)}`;
        root.querySelector('[data-export]').href = API.exportUrl({ ...state, limit: Math.min(data.total, 100000) });
        pager.innerHTML = '';
        const prev = h('<button class="btn sm">← Previous</button>');
        const next = h('<button class="btn sm">Next →</button>');
        prev.disabled = state.offset === 0;
        next.disabled = state.offset + state.limit >= data.total;
        prev.onclick = () => { state.offset = Math.max(0, state.offset - state.limit); load(); };
        next.onclick = () => { state.offset += state.limit; load(); };
        pager.append(prev, next, h(`<span class="small muted">Click any row for the full visit history.</span>`));
      } catch (e) { rowsPane.innerHTML = ''; rowsPane.appendChild(errorBox(e)); }
    }
    load();
    root.setFilter = (patch) => { Object.assign(state, patch, { offset: 0 }); load(); root.scrollIntoView({ behavior: 'smooth', block: 'start' }); };
    return root;
  }

  /* -------------------------------------------------- account detail ----- */
  async function showAccount(name) {
    const body = h('<div></div>');
    body.appendChild(loading());
    UI.modal(name, body, [], { wide: true });
    try {
      const d = await API.accountDetail(name);
      const s = d.summary;
      body.innerHTML = '';
      body.appendChild(h(`<div class="kpis">
        ${kpi('Best tier', s.best_tier || '—', `top score ${s.best_score}`)}
        ${kpi('Prospect IPs', s.prospect_ips, `${num(s.ips)} mapped in total`)}
        ${kpi('Known people', s.contacts, s.contacts ? 'from your CRM export' : 'upload a CRM export')}
        ${kpi('Page views', s.page_views, `${num(s.sessions)} sessions`)}
        ${kpi('Contact / Demo', `${num(s.contact_views)} / ${num(s.demo_views)}`, 'conversion page views')}
        ${kpi('Active', s.first_seen ? s.first_seen.split(' ')[0] : '—', `through ${(s.last_seen || '').split(' ')[0]}`)}
      </div>`));
      if (s.domain) body.appendChild(h(`<div class="note small">Domain <b>${esc(s.domain)}</b>${
        s.any_crawler_risk ? ' · at least one address on this account carries crawler risk — check before outreach' : ''}</div>`));

      const sec = (title, cols, rows, opts) => {
        if (!rows || !rows.length) return;
        const c = h(`<div class="card" style="margin-bottom:16px"><h3>${esc(title)}</h3><div class="body tight"></div></div>`);
        c.querySelector('.body').appendChild(UI.table(cols, rows, Object.assign({ headers: PROSPECT_HEADERS }, opts || {})));
        body.appendChild(c);
      };
      sec('People at this account', ['contact', 'title', 'email', 'requests', 'ips', 'contact_views', 'demo_views', 'reach', 'first_seen', 'last_seen'], d.contacts);
      sec('Addresses', ['rank', 'ip', 'tier', 'score', 'sessions', 'page_views', 'contact_views', 'demo_views', 'products', 'industries', 'crawler_risk', 'source', 'first_visit', 'last_visit'], d.ips, { onRow: (r) => showIp(r.ip) });
      sec('Campaigns that reached them', ['campaign', 'requests', 'ips', 'uids'], d.campaigns);
      sec('Pages viewed', ['path', 'category', 'product', 'industry', 'views', 'ips'], d.pages, { sortCol: 'views', sortDir: -1 });
      sec('How they arrived', ['source', 'requests'], d.sources);
    } catch (e) { body.innerHTML = ''; body.appendChild(errorBox(e)); }
  }

  /* --------------------------------------------------------- accounts ----- */
  async function accountsPage(view) {
    view.appendChild(loading());
    const first = await API.accounts({ limit: 200 });
    view.innerHTML = '';
    view.appendChild(h(`<div class="note small">Search a target company and see everything behind it — every address,
      every named person, the campaigns that reached them and the pages they read. Accounts are built from the
      mappings you upload, so load an IP → client file and a CRM UID → contact file to populate this view.</div>`));
    view.appendChild(ipMapPanel(() => App.render()));
    view.appendChild(uidMapPanel(() => App.render()));

    if (!first.total) {
      view.appendChild(h(`<div class="empty">No named accounts yet. Upload an IP → client mapping above and
        every address that resolves will appear here as a company.</div>`));
      return;
    }

    const card = h(`<div class="card">
      <h3>Accounts<span class="spacer"></span><span class="small muted" data-count></span></h3>
      <div class="body"><div class="filters">
        <label class="field"><span>Search company or domain</span>
          <input type="search" data-q placeholder="e.g. Woodgrove" style="width:260px"></label>
      </div></div>
      <div data-rows></div>
    </div>`);
    view.appendChild(card);
    const rowsPane = card.querySelector('[data-rows]');
    let timer = null;

    async function load(q) {
      rowsPane.innerHTML = ''; rowsPane.appendChild(loading());
      try {
        const d = await API.accounts({ limit: 500, search: q || null });
        rowsPane.innerHTML = '';
        rowsPane.appendChild(UI.table(d.columns, d.rows, {
          headers: PROSPECT_HEADERS, onRow: (r) => showAccount(r.account),
        }));
        card.querySelector('[data-count]').textContent =
          `${num(d.total)} account${d.total === 1 ? '' : 's'} — click any row for the full picture`;
      } catch (e) { rowsPane.innerHTML = ''; rowsPane.appendChild(errorBox(e)); }
    }
    card.querySelector('[data-q]').oninput = (e) => {
      clearTimeout(timer); const v = e.target.value; timer = setTimeout(() => load(v), 300);
    };
    load(null);
  }

  /* ------------------------------------------- CRM UID → contact upload -- */
  function uidMapPanel(onDone) {
    const card = h(`<div class="card" style="margin-bottom:16px">
      <h3>Campaign targets<span class="spacer"></span>
        <button class="btn sm" data-clear>Clear</button>
        <button class="btn sm primary" data-up>Upload CRM UID → contact file</button>
      </h3>
      <div class="body" data-status><span class="spinner"></span></div>
    </div>`);
    const status = card.querySelector('[data-status]');

    async function refresh() {
      try {
        const s = await API.uidMapStatus();
        status.innerHTML = s.mapping_size
          ? `<div class="note small" style="margin:0">Contact list loaded: <b>${num(s.mapping_size)}</b> UIDs,
             <b>${num(s.matched_uids)}</b> matched to clicks in this log, and
             <b>${num(s.reached_contact_or_demo)}</b> of those people went on to a Contact or Demo page.
             Names now appear below and in every prospect table.</div>`
          : `<div class="note small warn" style="margin:0">No contact list uploaded. This log carries
             <b>${num(s.uids_in_log)}</b> distinct campaign UIDs from <code>uid=</code> links — export those
             contacts from your marketing automation or CRM (columns like <code>UID</code>, <code>Name</code>,
             <code>Email</code>, <code>Company</code>) and upload to see who you are actually targeting.</div>`;
      } catch (e) { status.innerHTML = ''; status.appendChild(errorBox(e)); }
    }
    refresh();

    card.querySelector('[data-up]').onclick = () => {
      const body = h(`<div>
        <p class="muted small" style="margin-top:0">Any .xlsx or .csv with a UID column plus a Name,
        Email and/or Company column. Headers are auto-detected — <code>UID</code>, <code>CRM UID</code>,
        <code>Contact ID</code> and <code>Recipient ID</code> all work, as do separate
        <code>First Name</code> / <code>Last Name</code> columns.</p>
        <div class="drop" data-drop>Click to choose a file, or drop it here</div>
        <label style="display:block;margin-top:12px"><input type="checkbox" data-replace> Replace the existing contact list</label>
        <input type="file" accept=".xlsx,.xlsm,.csv,.tsv,.txt" style="display:none" data-file>
        <div data-out style="margin-top:12px"></div>
      </div>`);
      const { close } = UI.modal('Upload CRM UID → contact mapping', body);
      const file = body.querySelector('[data-file]');
      const drop = body.querySelector('[data-drop]');
      const out = body.querySelector('[data-out]');
      drop.onclick = () => file.click();
      drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('over'); };
      drop.ondragleave = () => drop.classList.remove('over');
      drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove('over'); if (e.dataTransfer.files[0]) send(e.dataTransfer.files[0]); };
      file.onchange = () => file.files[0] && send(file.files[0]);

      async function send(f) {
        out.innerHTML = ''; out.appendChild(loading('Matching UIDs…'));
        const fd = new FormData();
        fd.append('file', f);
        fd.append('replace', body.querySelector('[data-replace]').checked ? 'true' : 'false');
        try {
          const r = await API.form(`/api/${API.ds}/uid-map`, fd);
          out.innerHTML = `<div class="note small" style="margin:0">Loaded <b>${num(r.rows_loaded)}</b> contacts
            (${num(r.skipped_rows)} skipped). <b>${num(r.matched_uids)}</b> matched clicks in this log across
            <b>${num(r.campaigns_covered)}</b> campaign(s); <b>${num(r.reached_contact_or_demo)}</b> reached a
            Contact or Demo page. Columns used: UID=<code>${esc(r.columns_used.uid)}</code>,
            Contact=<code>${esc(r.columns_used.contact || '—')}</code>,
            Email=<code>${esc(r.columns_used.email || '—')}</code>,
            Company=<code>${esc(r.columns_used.company || '—')}</code>.</div>`;
          refresh();
          setTimeout(() => { close(); onDone && onDone(); }, 1600);
        } catch (e) { out.innerHTML = ''; out.appendChild(errorBox(e)); }
      }
    };

    card.querySelector('[data-clear]').onclick = async () => {
      if (!confirm('Remove the uploaded CRM contact list from this dataset?')) return;
      await API.clearUidMap(); refresh(); onDone && onDone();
    };
    return card;
  }

  /* ---------------------------------------------- campaign contact list --- */
  function contactPane(campaign) {
    const state = { campaign: campaign || null, limit: 100, offset: 0, identified_only: true };
    const root = h(`<div class="card" style="margin-bottom:16px">
      <h3>Who we are targeting<span class="spacer"></span><span class="small muted" data-count></span></h3>
      <div class="body"><div class="filters">
        <label class="field"><span>Show</span>
          <select data-f="identified_only">
            <option value="true" selected>Named contacts only</option>
            <option value="false">All UIDs, named or not</option>
          </select></label>
        <label class="field"><span>Behaviour</span>
          <select data-f="converted_only">
            <option value="false" selected>All clicks</option>
            <option value="true">Reached Contact or Demo</option>
          </select></label>
        <label class="field"><span>Search</span>
          <input type="search" data-f="search" placeholder="name, company, email…" style="width:200px"></label>
      </div>
      <div class="note small" style="margin:0 0 6px">A UID identifies the person the email was sent to. One
      UID can appear from many IP addresses because corporate mail scanners follow links automatically —
      the <b>Reach</b> column flags that, so a high click count is not mistaken for enthusiasm.</div>
      </div>
      <div data-rows></div>
      <div class="body" data-pager style="display:flex;gap:10px;align-items:center"></div>
    </div>`);
    const rowsPane = root.querySelector('[data-rows]');
    const pager = root.querySelector('[data-pager]');
    let timer = null;
    root.querySelector('.filters').addEventListener('input', (e) => {
      const f = e.target.dataset.f; if (!f) return;
      let v = e.target.value;
      if (f === 'identified_only' || f === 'converted_only') v = (v === 'true');
      state[f] = v; state.offset = 0;
      clearTimeout(timer); timer = setTimeout(load, e.target.type === 'search' ? 320 : 0);
    });

    async function load() {
      rowsPane.innerHTML = ''; rowsPane.appendChild(loading());
      try {
        const d = await API.campaignContacts(state);
        rowsPane.innerHTML = '';
        if (!d.mapping.mapped) {
          rowsPane.appendChild(h(`<div class="empty">Upload a CRM UID → contact file above to put names
            against the ${num(d.mapping.total_uids)} campaign UIDs in this log.</div>`));
          root.querySelector('[data-count]').textContent = '';
          pager.innerHTML = ''; return;
        }
        rowsPane.appendChild(UI.table(d.columns, d.rows, { headers: PROSPECT_HEADERS }));
        root.querySelector('[data-count]').textContent =
          `${num(d.total)} people — ${num(d.mapping.matched_uids)} of ${num(d.mapping.total_uids)} UIDs named, ` +
          `${num(d.mapping.converted)} reached Contact/Demo`;
        pager.innerHTML = '';
        const prev = h('<button class="btn sm">← Previous</button>');
        const next = h('<button class="btn sm">Next →</button>');
        prev.disabled = state.offset === 0;
        next.disabled = state.offset + state.limit >= d.total;
        prev.onclick = () => { state.offset = Math.max(0, state.offset - state.limit); load(); };
        next.onclick = () => { state.offset += state.limit; load(); };
        pager.append(prev, next);
      } catch (e) { rowsPane.innerHTML = ''; rowsPane.appendChild(errorBox(e)); }
    }
    load();
    root.setCampaign = (c) => { state.campaign = c || null; state.offset = 0; load(); };
    return root;
  }

  /* ------------------------------------------------- IP → account upload -- */
  function ipMapPanel(onDone) {
    const card = h(`<div class="card" style="margin-bottom:16px">
      <h3>Named accounts<span class="spacer"></span>
        <button class="btn sm" data-clear>Clear mapping</button>
        <button class="btn sm primary" data-up>Upload IP → domain / client file</button>
      </h3>
      <div class="body" data-status><span class="spinner"></span></div>
    </div>`);
    const status = card.querySelector('[data-status]');

    async function refresh() {
      try {
        const s = await API.ipMapStatus();
        status.innerHTML = s.mapping_size
          ? `<div class="note small" style="margin:0">Mapping loaded: <b>${num(s.mapping_size)}</b> rows,
             <b>${num(s.matched_ips)}</b> matched to IPs in this log, <b>${num(s.matched_prospects)}</b> of them
             prospects. Company and domain now appear in every prospect table.</div>`
          : `<div class="note small warn" style="margin:0">No IP mapping uploaded yet, so prospect tables show raw IPs.
             Upload a reverse-IP / ABM export (columns like <code>IP</code>, <code>Company</code>, <code>Domain</code>;
             <code>1.2.3.0/24</code> blocks are supported) to resolve accounts.</div>`;
      } catch (e) { status.innerHTML = ''; status.appendChild(errorBox(e)); }
    }
    refresh();

    card.querySelector('[data-up]').onclick = () => {
      const body = h(`<div>
        <p class="muted small">Any .xlsx or .csv with an IP column plus a Company and/or Domain column. Headers are
        auto-detected. Existing rows are updated in place unless you tick replace.</p>
        <div class="drop" data-drop>Click to choose a file, or drop it here</div>
        <label style="display:block;margin-top:12px"><input type="checkbox" data-replace> Replace the existing mapping</label>
        <input type="file" accept=".xlsx,.xlsm,.csv,.tsv,.txt" style="display:none" data-file>
        <div data-out style="margin-top:12px"></div>
      </div>`);
      const { close } = UI.modal('Upload IP → domain / client mapping', body);
      const file = body.querySelector('[data-file]');
      const drop = body.querySelector('[data-drop]');
      const out = body.querySelector('[data-out]');
      drop.onclick = () => file.click();
      drop.ondragover = (e) => { e.preventDefault(); drop.classList.add('over'); };
      drop.ondragleave = () => drop.classList.remove('over');
      drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove('over'); if (e.dataTransfer.files[0]) send(e.dataTransfer.files[0]); };
      file.onchange = () => file.files[0] && send(file.files[0]);

      async function send(f) {
        out.innerHTML = ''; out.appendChild(loading('Matching IPs…'));
        const fd = new FormData();
        fd.append('file', f);
        fd.append('replace', body.querySelector('[data-replace]').checked ? 'true' : 'false');
        try {
          const r = await API.form(`/api/${API.ds}/ip-map`, fd);
          out.innerHTML = `<div class="note small" style="margin:0">Loaded <b>${num(r.rows_loaded)}</b> mapping rows
            (${num(r.exact_rows)} exact, ${num(r.cidr_rows)} CIDR, ${num(r.skipped_rows)} skipped).
            <b>${num(r.matched_ips)}</b> matched this log; <b>${num(r.matched_prospects)}</b> are prospects.
            Columns used: IP=<code>${esc(r.columns_used.ip)}</code>,
            Company=<code>${esc(r.columns_used.company || '—')}</code>,
            Domain=<code>${esc(r.columns_used.domain || '—')}</code>.</div>`;
          refresh();
          setTimeout(() => { close(); onDone && onDone(); }, 1400);
        } catch (e) { out.innerHTML = ''; out.appendChild(errorBox(e)); }
      }
    };

    card.querySelector('[data-clear]').onclick = async () => {
      if (!confirm('Remove the uploaded IP → account mapping from this dataset?')) return;
      await API.clearIpMap(); refresh(); onDone && onDone();
    };
    return card;
  }

  /* ------------------------------------------------------------ dashboard - */
  async function dashboard(view) {
    view.appendChild(loading('Building dashboard…'));
    const d = await API.dashboard();
    view.innerHTML = '';
    const k = d.kpis;

    view.appendChild(h(`<div class="note small">
      <b>${esc(d.dataset.name)}</b> — ${esc(d.dataset.original_filename || '')} · coverage
      ${esc(k.coverage_start)} → ${esc(k.coverage_end)} UTC. Raw request volume is not lead volume:
      ${num(k.bot_requests)} requests (${(100 * k.bot_requests / Math.max(1, k.total_requests)).toFixed(1)}%) came from
      declared automation, and behavioural crawlers with normal browser agents are on top of that.
    </div>`));

    view.appendChild(h(`<div class="kpis">
      ${kpi('Total web requests', k.total_requests, 'raw activity set')}
      ${kpi('Unique IP addresses', k.unique_ips, 'not the same as prospects')}
      ${kpi('Eligible intent IPs', k.eligible_ips, 'at least one intent signal', 'ok')}
      ${kpi('A — Immediate', k.a_immediate, 'work these first', 'a1')}
      ${kpi('A — High', k.a_high, 'outreach this week', 'a2')}
      ${kpi('B — Warm', k.b_warm, 'enrich and nurture', 'b')}
      ${kpi('C — Nurture', k.c_nurture, 'ABM / nurture pool')}
      ${kpi('Contact / Demo IPs', k.contact_or_demo_ips, `${num(k.contact_demo_plus_eval_ips)} also evaluated product content`)}
      ${kpi('Top 100 avg score', k.top100_avg_score, `top 500 avg ${k.top500_avg_score}`)}
      ${kpi('Named accounts', k.named_accounts, k.named_accounts ? 'from your IP mapping' : 'upload an IP mapping')}
      ${kpi('Unique URLs', k.unique_urls, `${num(k.unique_user_agents)} user agents`)}
      ${kpi('External referral requests', k.external_ref_requests, 'most traffic has no referrer')}
    </div>`));

    const grid = h('<div class="grid c2"></div>');
    const P = d.pies;
    const goto = (page, filterKey) => (slice) => {
      if (!slice || String(slice.label).startsWith('Other (')) return;
      App.go(page, { [filterKey]: slice.label });
    };
    grid.appendChild(UI.chartTableCard({ ...P.industry, columns: P.industry.columns, rows: P.industry.table,
      headers: PROSPECT_HEADERS, onSlice: goto('industry', 'industry') }));
    grid.appendChild(UI.chartTableCard({ ...P.product, columns: P.product.columns, rows: P.product.table,
      headers: PROSPECT_HEADERS, onSlice: goto('product', 'product') }));
    grid.appendChild(UI.chartTableCard({ ...P.campaign, columns: P.campaign.columns, rows: P.campaign.table,
      headers: PROSPECT_HEADERS, onSlice: goto('campaign', 'campaign') }));
    grid.appendChild(UI.chartTableCard({ ...P.ip, columns: P.ip.columns, rows: P.ip.table,
      headers: PROSPECT_HEADERS, onSlice: (s) => App.go('ip', { tier: s.label }) }));
    grid.appendChild(UI.chartTableCard({ ...P.source, columns: P.source.columns, rows: P.source.table,
      headers: PROSPECT_HEADERS, onSlice: () => App.go('sources') }));

    const act = h(`<div class="card"><h3>Daily request volume</h3><div class="body"><div class="chart-wrap"><canvas></canvas></div></div></div>`);
    grid.appendChild(act);
    view.appendChild(grid);
    UI.defer(() => UI.line(act.querySelector('canvas'),
      d.activity.map((a) => a.label), [{ label: 'Requests', data: d.activity.map((a) => a.value) }]));
  }

  /* ------------------------------------------- industry / product / campaign */
  function dimensionPage(kind) {
    const conf = {
      industry: { title: 'Industry prospects', fetch: () => API.industries(), filter: 'industry',
                  lead: 'Which verticals are researching eGain, and how many prospect IPs sit behind each one.' },
      product: { title: 'Product prospects', fetch: () => API.products(), filter: 'product',
                 lead: 'Product and solution interest inferred from the pages each prospect actually read.' },
      campaign: { title: 'Campaign prospects', fetch: () => API.campaigns(), filter: 'campaign',
                  lead: 'Traffic attributed to a utm_campaign. CRM UIDs on these URLs resolve to real contacts — a stronger identity path than reverse-IP lookup.' },
    }[kind];

    return async (view, params) => {
      view.appendChild(loading());
      const data = await conf.fetch();
      view.innerHTML = '';
      view.appendChild(h(`<div class="note small">${esc(conf.lead)}</div>`));

      let pane, contacts;
      const rerender = () => App.render();
      view.appendChild(ipMapPanel(rerender));
      if (kind === 'campaign') view.appendChild(uidMapPanel(rerender));

      const slices = data.rows.slice(0, 10).map((r) => ({ label: r.key, value: r.prospects }));
      const rest = data.rows.slice(10).reduce((a, r) => a + (r.prospects || 0), 0);
      if (rest) slices.push({ label: `Other (${data.rows.length - 10})`, value: rest });

      const grid = h('<div class="grid c2" style="margin-bottom:16px"></div>');
      grid.appendChild(UI.chartTableCard({
        title: `${conf.title} — share`, slices, columns: data.columns, rows: data.rows,
        headers: PROSPECT_HEADERS,
        onSlice: (s) => {
          if (String(s.label).startsWith('Other (')) return;
          pane.setFilter({ [conf.filter]: s.label });
          if (contacts) contacts.setCampaign(s.label);
        },
        onRow: (r) => {
          pane.setFilter({ [conf.filter]: r.key });
          if (contacts) contacts.setCampaign(r.key);
        },
        note: 'Click a slice or a table row to filter the prospect list below.',
      }));
      const barCard = h(`<div class="card"><h3>Prospects by ${esc(kind)}</h3><div class="body"><div class="chart-wrap tall"><canvas></canvas></div></div></div>`);
      grid.appendChild(barCard);
      view.appendChild(grid);
      UI.defer(() => {
        const top = data.rows.slice(0, 12);
        UI.bar(barCard.querySelector('canvas'), top.map((r) => r.key),
          [{ label: 'Prospect IPs', data: top.map((r) => r.prospects) }], { horizontal: true });
      });

      const selected = (params && params[conf.filter]) || '';
      const bar2 = h(`<div class="filters" style="align-items:center">
        <label class="field"><span>${esc(UI.titleCase(kind))}</span>
          <select data-pick>
            <option value="">All ${esc(kind)}s</option>
            ${data.rows.map((r) => `<option ${r.key === selected ? 'selected' : ''} value="${esc(r.key)}">${esc(r.key)} (${num(r.prospects)})</option>`).join('')}
          </select></label>
      </div>`);
      view.appendChild(bar2);
      pane = prospectPane(selected ? { [conf.filter]: selected } : {}, { title: `${conf.title} — prospect list` });
      if (kind === 'campaign') {
        contacts = contactPane(selected);
        view.appendChild(contacts);
      }
      bar2.querySelector('[data-pick]').onchange = (e) => {
        const v = e.target.value || null;
        pane.setFilter({ [conf.filter]: v });
        if (contacts) contacts.setCampaign(v);
      };
      view.appendChild(pane);
    };
  }

  /* ------------------------------------------------------------- sources -- */
  async function sources(view) {
    view.appendChild(loading());
    const s = await API.sources(500);
    view.innerHTML = '';
    view.appendChild(h(`<div class="note small">Top ${num(s.rows.length)} referring hosts of ${num(s.total_sources)} seen.
      ${num(s.direct_requests)} requests carried no referrer at all, so external discovery is only part of the picture.</div>`));

    const grid = h('<div class="grid c2" style="margin-bottom:16px"></div>');
    grid.appendChild(UI.chartTableCard({
      title: 'Prospect IPs by source class',
      slices: s.by_class.filter((c) => c.prospects).map((c) => ({ label: c.key, value: c.prospects })),
      columns: ['key', 'prospects', 'unique_ips', 'requests'], rows: s.by_class, headers: PROSPECT_HEADERS,
    }));
    const barCard = h('<div class="card"><h3>Top referrers by request volume</h3><div class="body"><div class="chart-wrap tall"><canvas></canvas></div></div></div>');
    grid.appendChild(barCard);
    view.appendChild(grid);
    UI.defer(() => {
      const top = s.rows.slice(0, 15);
      UI.bar(barCard.querySelector('canvas'), top.map((r) => r.ref_host),
        [{ label: 'Requests', data: top.map((r) => r.requests) }], { horizontal: true });
    });

    const card = h('<div class="card"><h3>Top 500 sources<span class="spacer"></span><span class="small muted">sortable — click any column</span></h3><div class="body tight"></div></div>');
    card.querySelector('.body').appendChild(UI.table(s.columns, s.rows,
      { headers: PROSPECT_HEADERS, sortCol: 'requests', sortDir: -1 }));
    view.appendChild(card);
  }

  /* --------------------------------------------------------- IP analysis -- */
  async function ipAnalysis(view, params) {
    view.appendChild(loading());
    const a = await API.ipAnalysis();
    view.innerHTML = '';
    const t = a.totals;
    view.appendChild(h(`<div class="kpis">
      ${kpi('Unique IPs', t.unique_ips, 'raw addresses in the log')}
      ${kpi('Eligible intent IPs', t.eligible_ips, 'at least one intent signal', 'ok')}
      ${kpi('IPs with 2+ requests', t.ips_2plus, `${(100 * t.ips_2plus / Math.max(1, t.unique_ips)).toFixed(1)}% repeat activity`)}
      ${kpi('IPs with 5+ requests', t.ips_5plus, 'better engagement signal')}
      ${kpi('IPs with 10+ requests', t.ips_10plus, 'crawler filtering required')}
      ${kpi('Declared-bot IPs', t.bot_ua_ips, 'excluded from prospects')}
      ${kpi('Contact / Demo IPs', t.contact_or_demo_ips, 'strongest raw sales pool')}
      ${kpi('+ product evaluation', t.contact_demo_plus_eval_ips, 'highest-intent segment', 'a1')}
      ${kpi('Named accounts', t.named_accounts, 'from your IP mapping')}
    </div>`));

    view.appendChild(ipMapPanel(() => App.render()));

    const grid = h('<div class="grid c3" style="margin-bottom:16px"></div>');
    grid.appendChild(UI.chartTableCard({ title: 'Eligible IPs by tier',
      slices: a.tiers.map((r) => ({ label: r.key, value: r.ips })), columns: ['key', 'ips', 'avg_score'],
      rows: a.tiers, headers: PROSPECT_HEADERS }));
    grid.appendChild(UI.chartTableCard({ title: 'All IPs by crawler risk',
      slices: a.risk.map((r) => ({ label: r.key, value: r.ips })), columns: ['key', 'ips'], rows: a.risk,
      headers: PROSPECT_HEADERS }));
    grid.appendChild(UI.chartTableCard({ title: 'Prospect IPs by traffic source',
      slices: a.sources.map((r) => ({ label: r.key, value: r.ips })), columns: ['key', 'ips'], rows: a.sources,
      headers: PROSPECT_HEADERS }));
    const hist = h('<div class="card"><h3>Request depth per IP</h3><div class="body"><div class="chart-wrap"><canvas></canvas></div></div></div>');
    const scoreHist = h('<div class="card"><h3>Intent score distribution</h3><div class="body"><div class="chart-wrap"><canvas></canvas></div></div></div>');
    grid.append(hist, scoreHist);
    view.appendChild(grid);
    UI.defer(() => {
      UI.bar(hist.querySelector('canvas'), a.request_buckets.map((b) => b.bucket + ' request(s)'),
        [{ label: 'IPs', data: a.request_buckets.map((b) => b.ips) }]);
      UI.bar(scoreHist.querySelector('canvas'), a.score_histogram.map((b) => `${b.band}–${b.band + 9}`),
        [{ label: 'Eligible IPs', data: a.score_histogram.map((b) => b.ips) }]);
    });

    view.appendChild(prospectPane(params || {}, { title: 'Eligible IPs — full prospect list' }));
  }

  /* ---------------------------------------------------- recommendations --- */
  async function recommendations(view) {
    view.appendChild(loading());
    const r = await API.recommendations();
    view.innerHTML = '';

    const plays = h('<div class="card" style="margin-bottom:16px"><h3>Recommended sales motion</h3><div class="body"></div></div>');
    r.plays.forEach((p, i) => plays.querySelector('.body').appendChild(
      h(`<div class="play"><b>${i + 1}. ${esc(p.title)}</b><span class="muted">${esc(p.detail)}</span></div>`)));
    view.appendChild(plays);

    for (const t of r.tiers) {
      const block = h(`<div class="card tierblock">
        <h3><span class="pill t-${UI.slug(t.tier)}">${esc(t.tier)}</span>
          <span>${esc(t.action)}</span><span class="spacer"></span>
          <span class="small muted">${num(t.ips)} IPs · avg score ${t.avg_score || 0} ·
            ${num(t.with_uid)} with CRM UID · ${num(t.named_accounts)} named</span>
          <button class="btn sm" data-all>Open full list</button>
        </h3>
        <div class="body"><div class="small muted" style="margin-bottom:10px">${esc(t.guidance)}</div></div>
        <div data-rows></div>
      </div>`);
      block.querySelector('[data-rows]').appendChild(UI.table(
        ['rank', 'score', 'ip', 'company', 'source', 'uid', 'products', 'industries', 'why', 'next_action'],
        t.top, { headers: PROSPECT_HEADERS, onRow: (row) => showIp(row.ip) }));
      block.querySelector('[data-all]').onclick = () => App.go('ip', { tier: t.tier });
      view.appendChild(block);
    }

    const guide = h(`<div class="grid c2">
      <div class="card"><h3>Sales intent scoring</h3><div class="body tight"></div></div>
      <div class="card"><h3>Important caveats</h3><div class="body"><ul style="margin:0;padding-left:18px">
        ${r.caveats.map((c) => `<li style="margin-bottom:7px">${esc(c)}</li>`).join('')}</ul></div></div>
    </div>`);
    guide.querySelector('.body.tight').appendChild(UI.table(['signal', 'weight', 'reason'],
      r.scoring_guide.weights.map(([signal, weight, reason]) => ({ signal, weight, reason })), {}));
    view.appendChild(guide);
  }

  /* --------------------------------------------------------------- ask ai - */
  async function askAI(view) {
    const status = await API.askStatus();
    view.innerHTML = '';
    const sid = App.aiSession;

    const wrap = h(`<div>
      <div class="note small">Ask anything about this weblog. Claude queries the dataset directly and answers with
        text, tables and charts. Follow-up questions reuse the same session, so you can drill in.</div>
      <div class="chips"></div>
      <div class="chat"></div>
      <div class="composer"><div class="row">
        <textarea placeholder="e.g. Which 10 accounts should I call first this week, and why?" rows="2"></textarea>
        <button class="btn primary" data-send>Ask</button>
        <button class="btn" data-reset title="Start a new session">New</button>
      </div>
      <div class="small muted" style="margin:7px 0 0">Enter to send · Shift+Enter for a new line</div></div>
    </div>`);
    view.appendChild(wrap);

    const chat = wrap.querySelector('.chat');
    const ta = wrap.querySelector('textarea');
    const chips = wrap.querySelector('.chips');

    if (!status.enabled) {
      chat.appendChild(h(`<div class="note err">Ask AI is not configured. Add <code>ANTHROPIC_API_KEY=…</code> to the
        <code>.env</code> file next to <code>run.sh</code> and restart ESP.</div>`));
      wrap.querySelector('[data-send]').disabled = true;
      ta.disabled = true;
    }
    status.suggestions.forEach((s) => {
      const c = h(`<button class="chip">${esc(s)}</button>`);
      c.onclick = () => { ta.value = s; send(); };
      chips.appendChild(c);
    });

    App.aiHistory.forEach((m) => chat.appendChild(bubble(m.role, m.payload)));
    chat.scrollIntoView({ block: 'end' });

    function bubble(role, payload) {
      const el = h(`<div class="msg ${role === 'user' ? 'user' : 'ai'}">
        <div class="av">${role === 'user' ? 'You' : 'AI'}</div><div class="bubble"></div></div>`);
      const b = el.querySelector('.bubble');
      if (role === 'user') { b.textContent = payload; return el; }
      if (payload.pending) {
        b.innerHTML = '<span class="spinner"></span> <span data-tick>Querying the dataset…</span>';
        return el;
      }
      if (payload.error) { b.innerHTML = `<div class="note err" style="margin:0">${esc(payload.error)}</div>`; return el; }

      b.innerHTML = UI.markdown(payload.answer);
      (payload.charts || []).forEach((c) => {
        const card = h(`<div style="margin-top:12px"><div class="small muted" style="margin-bottom:5px"><b>${esc(c.title)}</b></div>
          <div class="chart-wrap"><canvas></canvas></div></div>`);
        b.appendChild(card);
        UI.defer(() => {
          const cv = card.querySelector('canvas');
          const series = (c.series || []).map((s) => ({ label: s.label, data: s.data }));
          if (c.type === 'pie' || c.type === 'doughnut') {
            UI.pie(cv, (c.labels || []).map((l, i) => ({ label: l, value: (series[0] && series[0].data[i]) || 0 })),
              { doughnut: c.type === 'doughnut' });
          } else if (c.type === 'line') UI.line(cv, c.labels || [], series);
          else UI.bar(cv, c.labels || [], series, { horizontal: c.type === 'horizontalBar' });
        });
      });
      (payload.tables || []).forEach((t) => {
        const card = h(`<div style="margin-top:12px"><div class="small muted" style="margin-bottom:5px"><b>${esc(t.title)}</b></div></div>`);
        const rows = (t.rows || []).map((r) => Object.fromEntries((t.columns || []).map((c, i) => [c, r[i]])));
        card.appendChild(UI.table(t.columns || [], rows, {}));
        b.appendChild(card);
      });
      if ((payload.queries || []).length) {
        const det = h(`<details class="sql"><summary>${payload.queries.length} data quer${payload.queries.length === 1 ? 'y' : 'ies'} run</summary></details>`);
        payload.queries.forEach((q) => det.appendChild(h(
          `<div><div class="small muted" style="margin-top:7px">${esc(q.purpose || '')}${q.error ? ' — <span style="color:var(--a1)">' + esc(q.error) + '</span>' : ''}</div>
           <pre><code>${esc(q.sql)}</code></pre></div>`)));
        b.appendChild(det);
      }
      return el;
    }

    async function send() {
      const q = ta.value.trim();
      if (!q) return;
      ta.value = '';
      App.aiHistory.push({ role: 'user', payload: q });
      chat.appendChild(bubble('user', q));
      const pending = bubble('ai', { pending: true });
      chat.appendChild(pending);
      pending.scrollIntoView({ behavior: 'smooth', block: 'end' });
      try {
        const tick = pending.querySelector('[data-tick]');
        const r = await API.ask(q, sid, (secs, msg) => {
          if (tick) tick.textContent = `${msg || 'Querying the dataset…'} (${secs}s)`;
        });
        const payload = { answer: r.answer, charts: r.charts, tables: r.tables, queries: r.queries };
        App.aiHistory.push({ role: 'ai', payload });
        pending.replaceWith(bubble('ai', payload));
      } catch (e) {
        const payload = { error: e.message || String(e) };
        App.aiHistory.push({ role: 'ai', payload });
        pending.replaceWith(bubble('ai', payload));
      }
      chat.lastElementChild.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }

    wrap.querySelector('[data-send]').onclick = send;
    wrap.querySelector('[data-reset]').onclick = async () => {
      const fd = new FormData(); fd.append('session_id', sid);
      try { await API.form(`/api/${API.ds}/ask/reset`, fd); } catch (e) { /* session may not exist yet */ }
      App.aiHistory = []; App.aiSession = 's' + Math.random().toString(36).slice(2, 9);
      App.render();
    };
    ta.onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } };
    ta.focus();
  }

  return {
    dashboard,
    accounts: accountsPage,
    showAccount,
    industry: dimensionPage('industry'),
    product: dimensionPage('product'),
    campaign: dimensionPage('campaign'),
    recommendations,
    askAI,
    sources,
    ipAnalysis,
    showIp,
    loading,
    errorBox,
  };
})();
