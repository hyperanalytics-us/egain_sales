/* Thin fetch wrapper.  Every call is scoped to the active dataset. */
/* Base path the app is mounted at ("/" or "/esp/"), injected by the server. */
const BASE = (window.ESP_BASE || '/').replace(/\/+$/, '');
const url = (p) => BASE + p;

const API = {
  ds: null,
  base: BASE,
  onAuthRequired: null,

  async req(path, opts = {}) {
    const res = await fetch(url(path), { credentials: 'same-origin', ...opts });
    const ct = res.headers.get('content-type') || '';
    const body = ct.includes('application/json') ? await res.json() : await res.text();
    if (res.status === 401 && body && body.auth_required) {
      if (this.onAuthRequired) this.onAuthRequired();
      throw new Error(body.error || 'Sign in to continue.');
    }
    if (!res.ok) throw new Error((body && body.error) || res.statusText || 'Request failed');
    return body;
  },
  get(path)          { return this.req(path); },
  post(path, json)   { return this.req(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(json || {}) }); },
  del(path)          { return this.req(path, { method: 'DELETE' }); },
  form(path, fd)     { return this.req(path, { method: 'POST', body: fd }); },

  d(path, params) {
    const qs = params ? '?' + new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== '' && v !== false)
    ) : '';
    return this.get(`/api/${this.ds}${path}${qs}`);
  },

  datasets()            { return this.get('/api/datasets'); },
  job(id)               { return this.get(`/api/jobs/${id}`); },
  askStatus()           { return this.get('/api/ask/status'); },
  dashboard()           { return this.d('/dashboard'); },
  prospects(p)          { return this.d('/prospects', p); },
  industries()          { return this.d('/industries'); },
  products()            { return this.d('/products'); },
  campaigns()           { return this.d('/campaigns'); },
  sources(limit)        { return this.d('/sources', { limit }); },
  ipAnalysis()          { return this.d('/ip-analysis'); },
  ipDetail(ip)          { return this.d(`/ip/${encodeURIComponent(ip)}`); },
  pages(p)              { return this.d('/pages', p); },
  recommendations()     { return this.d('/recommendations'); },
  ipMapStatus()         { return this.d('/ip-map'); },
  clearIpMap()          { return this.del(`/api/${this.ds}/ip-map`); },
  askStart(question, sid) { return this.post(`/api/${this.ds}/ask`, { question, session_id: sid }); },

  /* Ask AI runs as a background job so no single request stays open for minutes
     - long requests are cut off by CDNs and proxies (Cloudflare returns 524). */
  async ask(question, sid, onTick) {
    const { job_id } = await this.askStart(question, sid);
    const started = Date.now();
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      const job = await this.job(job_id);
      if (job.status === 'done') return job.result;
      if (job.status === 'error') throw new Error(job.message || 'Ask AI failed.');
      if (onTick) onTick(Math.round((Date.now() - started) / 1000), job.message);
      if (Date.now() - started > 10 * 60 * 1000) throw new Error('Timed out after 10 minutes.');
    }
  },
  exportUrl(p) {
    const qs = new URLSearchParams(Object.entries(p || {}).filter(([, v]) => v !== null && v !== undefined && v !== '' && v !== false));
    return url(`/api/${this.ds}/export/prospects.csv?${qs}`);
  },

  authStatus()      { return this.get('/api/auth/status'); },
  login(password)   { return this.post('/api/auth/login', { password }); },
  logout()          { return this.post('/api/auth/logout'); },
};
