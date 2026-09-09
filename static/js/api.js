/* Thin fetch wrapper.  Every call is scoped to the active dataset. */
const API = {
  ds: null,

  async req(path, opts = {}) {
    const res = await fetch(path, opts);
    const ct = res.headers.get('content-type') || '';
    const body = ct.includes('application/json') ? await res.json() : await res.text();
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
  ask(question, sid)    { return this.post(`/api/${this.ds}/ask`, { question, session_id: sid }); },
  exportUrl(p) {
    const qs = new URLSearchParams(Object.entries(p || {}).filter(([, v]) => v !== null && v !== undefined && v !== '' && v !== false));
    return `/api/${this.ds}/export/prospects.csv?${qs}`;
  },
};
