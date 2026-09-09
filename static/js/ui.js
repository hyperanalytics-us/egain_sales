/* Shared UI primitives: formatting, tables, charts, modals, markdown. */

const UI = (() => {
  const nf = new Intl.NumberFormat('en-US');

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const num = (v) => (v === null || v === undefined || v === '' ? '' : (typeof v === 'number' ? nf.format(v) : v));

  const slug = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

  const titleCase = (s) => String(s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

  /* Charts must not be scheduled with requestAnimationFrame: a backgrounded tab
     suspends rAF indefinitely, so a chart queued while the user is on another tab
     would never be drawn.  setTimeout keeps running when hidden. */
  const defer = (fn) => setTimeout(fn, 0);

  function h(html) { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; }

  /* Chart.js palette that stays legible in both themes. */
  const PALETTE = ['#0b57d0', '#0a8f8f', '#ef6c00', '#7b3fb5', '#1b7f4d', '#c62828',
                   '#f9a825', '#00838f', '#5d6d8a', '#ad1457', '#2e7d32', '#4527a0'];

  const chartRegistry = new Map();

  function chartDefaults() {
    const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    return {
      tick: dark ? '#a8b4cc' : '#55627a',
      grid: dark ? 'rgba(255,255,255,.07)' : 'rgba(18,32,58,.07)',
      label: dark ? '#e8edf7' : '#12203a',
    };
  }

  function drawChart(canvas, config) {
    // Re-rendering a page throws away its canvases; their Chart instances would
    // otherwise stay alive (with their resize observers) forever.
    for (const [cv, chart] of chartRegistry) {
      if (!cv.isConnected) { chart.destroy(); chartRegistry.delete(cv); }
    }
    const prev = chartRegistry.get(canvas);
    if (prev) prev.destroy();
    const c = new Chart(canvas.getContext('2d'), config);
    chartRegistry.set(canvas, c);
    return c;
  }

  function pie(canvas, slices, opts = {}) {
    const d = chartDefaults();
    return drawChart(canvas, {
      type: opts.doughnut === false ? 'pie' : 'doughnut',
      data: {
        labels: slices.map((s) => s.label),
        datasets: [{
          data: slices.map((s) => s.value),
          backgroundColor: slices.map((_, i) => PALETTE[i % PALETTE.length]),
          borderWidth: 2,
          borderColor: getComputedStyle(document.body).getPropertyValue('--panel').trim() || '#fff',
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: opts.doughnut === false ? 0 : '52%',
        onClick: opts.onClick ? (evt, els) => { if (els.length) opts.onClick(slices[els[0].index]); } : undefined,
        plugins: {
          legend: { position: 'right', labels: { color: d.label, boxWidth: 11, boxHeight: 11, padding: 9, font: { size: 11.5 } } },
          tooltip: {
            callbacks: {
              label(ctx) {
                const total = ctx.dataset.data.reduce((a, b) => a + b, 0) || 1;
                return ` ${ctx.label}: ${nf.format(ctx.parsed)} (${(100 * ctx.parsed / total).toFixed(1)}%)`;
              },
            },
          },
        },
      },
    });
  }

  function bar(canvas, labels, series, opts = {}) {
    const d = chartDefaults();
    return drawChart(canvas, {
      type: 'bar',
      data: {
        labels,
        datasets: series.map((s, i) => ({
          label: s.label, data: s.data,
          backgroundColor: PALETTE[i % PALETTE.length], borderRadius: 4, maxBarThickness: 34,
        })),
      },
      options: {
        indexAxis: opts.horizontal ? 'y' : 'x',
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: series.length > 1, labels: { color: d.label } } },
        scales: {
          x: { ticks: { color: d.tick, autoSkip: true, maxRotation: opts.horizontal ? 0 : 45 }, grid: { color: d.grid } },
          y: { ticks: { color: d.tick }, grid: { color: d.grid }, beginAtZero: true },
        },
      },
    });
  }

  function line(canvas, labels, series) {
    const d = chartDefaults();
    return drawChart(canvas, {
      type: 'line',
      data: {
        labels,
        datasets: series.map((s, i) => ({
          label: s.label, data: s.data, borderColor: PALETTE[i % PALETTE.length],
          backgroundColor: PALETTE[i % PALETTE.length] + '22', fill: true, tension: .3,
          pointRadius: 2, borderWidth: 2,
        })),
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: series.length > 1, labels: { color: d.label } } },
        scales: {
          x: { ticks: { color: d.tick, maxRotation: 45, autoSkip: true }, grid: { color: d.grid } },
          y: { ticks: { color: d.tick }, grid: { color: d.grid }, beginAtZero: true },
        },
      },
    });
  }

  /* ----------------------------------------------------------- tables ---- */
  const NUMERIC = /^(rank|score|views|ips|prospects|requests|sessions|uids|days|count|n|avg_score|.*_views|.*_ips|page_views|unique_pages|active_days|named_accounts|a_immediate|a_high|b_warm|c_nurture|intent_ips)$/;

  function cellClass(col, val) {
    if (NUMERIC.test(col) || typeof val === 'number') return 'num';
    if (col === 'why' || col === 'risk_reason' || col === 'score_components' || col === 'next_action'
        || col === 'top_intent_pages' || col === 'detail') return 'wrap';
    if (col === 'ip' || col === 'path' || col === 'uid' || col === 'ref_host' || col === 'domain') return 'mono';
    return '';
  }

  function renderCell(col, val, row) {
    if (col === 'tier') return `<span class="pill t-${slug(val)}">${esc(val)}</span>`;
    if (col === 'crawler_risk') return `<span class="pill r-${slug(val)}">${esc(val)}</span>`;
    if (col === 'score') {
      const v = Number(val) || 0;
      return `<span class="score-bar"><b>${v}</b><i><span style="width:${v}%"></span></i></span>`;
    }
    if (col === 'company' && !val) return '<span class="muted">—</span>';
    if (val === null || val === undefined || val === '') return '';
    return esc(num(val));
  }

  /**
   * Sortable table.  `opts.headers` overrides column labels, `opts.onRow(row)`
   * makes rows clickable.
   */
  function table(columns, rows, opts = {}) {
    const wrap = h('<div class="table-scroll"></div>');
    if (!rows.length) { wrap.innerHTML = '<div class="empty">No rows match the current filters.</div>'; return wrap; }
    let sortCol = opts.sortCol || null;
    let sortDir = opts.sortDir || -1;
    const headers = opts.headers || {};

    const draw = () => {
      let data = rows;
      if (sortCol) {
        data = rows.slice().sort((a, b) => {
          const x = a[sortCol], y = b[sortCol];
          if (typeof x === 'number' && typeof y === 'number') return (x - y) * sortDir;
          return String(x ?? '').localeCompare(String(y ?? ''), undefined, { numeric: true }) * sortDir;
        });
      }
      wrap.innerHTML = `<table><thead><tr>${columns.map((c) => {
        const label = headers[c] || titleCase(c);
        const arrow = sortCol === c ? `<span class="arrow">${sortDir > 0 ? '▲' : '▼'}</span>` : '';
        return `<th data-c="${esc(c)}" class="${NUMERIC.test(c) ? 'num' : ''}">${esc(label)} ${arrow}</th>`;
      }).join('')}</tr></thead><tbody>${
        data.map((r, i) => `<tr class="${opts.onRow ? 'clickable' : ''}" data-i="${i}">${
          columns.map((c) => `<td class="${cellClass(c, r[c])}">${renderCell(c, r[c], r)}</td>`).join('')
        }</tr>`).join('')
      }</tbody></table>`;
      wrap.querySelectorAll('th').forEach((th) => th.onclick = () => {
        const c = th.dataset.c;
        if (sortCol === c) sortDir = -sortDir; else { sortCol = c; sortDir = -1; }
        draw();
      });
      if (opts.onRow) {
        wrap.querySelectorAll('tbody tr').forEach((tr) => tr.onclick = () => opts.onRow(data[+tr.dataset.i]));
      }
    };
    draw();
    return wrap;
  }

  /** Card with a chart on top and a table view behind a toggle. */
  function chartTableCard({ title, slices, columns, rows, headers, kind = 'pie', onSlice, onRow, note }) {
    const id = 'c' + Math.random().toString(36).slice(2, 9);
    const card = h(`
      <div class="card">
        <h3>${esc(title)}<span class="spacer"></span>
          <span class="toggle">
            <button data-v="chart" class="on">Chart</button>
            <button data-v="table">Table</button>
          </span>
        </h3>
        <div class="body tight">
          <div data-pane="chart" class="chart-wrap"><canvas id="${id}"></canvas></div>
          <div data-pane="table" style="display:none"></div>
        </div>
      </div>`);
    if (note) card.querySelector('.body').insertAdjacentElement('afterbegin', h(`<div class="note small" style="margin:12px 16px 0">${note}</div>`));

    const canvas = card.querySelector('canvas');
    const tablePane = card.querySelector('[data-pane="table"]');
    tablePane.appendChild(table(columns, rows, { headers, onRow, sortCol: columns[1], sortDir: -1 }));

    const panes = card.querySelectorAll('[data-pane]');
    card.querySelectorAll('.toggle button').forEach((b) => b.onclick = () => {
      card.querySelectorAll('.toggle button').forEach((x) => x.classList.toggle('on', x === b));
      panes.forEach((p) => p.style.display = p.dataset.pane === b.dataset.v ? '' : 'none');
    });

    defer(() => {
      if (!slices || !slices.length) {
        canvas.parentElement.innerHTML = '<div class="empty">Nothing to chart yet.</div>';
        return;
      }
      if (kind === 'bar') bar(canvas, slices.map((s) => s.label), [{ label: title, data: slices.map((s) => s.value) }], { horizontal: true });
      else pie(canvas, slices, { onClick: onSlice });
    });
    return card;
  }

  /* ----------------------------------------------------------- overlay --- */
  function modal(title, bodyNode, footNodes = [], opts = {}) {
    const root = document.getElementById('overlay-root');
    const overlay = h(`<div class="overlay"><div class="modal ${opts.wide ? 'wide' : ''}">
      <h2>${esc(title)}<button class="close-x" aria-label="Close">×</button></h2>
      <div class="body"></div></div></div>`);
    overlay.querySelector('.body').appendChild(bodyNode);
    if (footNodes.length) {
      const foot = h('<div class="foot"></div>');
      footNodes.forEach((n) => foot.appendChild(n));
      overlay.querySelector('.modal').appendChild(foot);
    }
    const close = () => overlay.remove();
    overlay.querySelector('.close-x').onclick = close;
    overlay.onclick = (e) => { if (e.target === overlay) close(); };
    document.addEventListener('keydown', function onKey(e) {
      if (e.key === 'Escape') { close(); document.removeEventListener('keydown', onKey); }
    });
    root.appendChild(overlay);
    return { overlay, close };
  }

  function toast(message, kind = 'ok') {
    const t = h(`<div class="note ${kind === 'err' ? 'err' : ''}" style="position:fixed;right:20px;bottom:20px;z-index:200;box-shadow:var(--shadow);background:var(--panel)">${esc(message)}</div>`);
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 4800);
  }

  /* ------------------------------------------------------- markdown ------ */
  function markdown(src) {
    const lines = String(src || '').split('\n');
    let out = '', inCode = false, listType = null;
    const inline = (s) => esc(s)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    const closeList = () => { if (listType) { out += `</${listType}>`; listType = null; } };

    for (const raw of lines) {
      const l = raw.replace(/\s+$/, '');
      if (/^```/.test(l)) { closeList(); inCode = !inCode; out += inCode ? '<pre><code>' : '</code></pre>'; continue; }
      if (inCode) { out += esc(raw) + '\n'; continue; }
      const hm = l.match(/^(#{1,6})\s+(.*)$/);
      if (hm) { closeList(); out += `<h${Math.min(6, hm[1].length + 2)}>${inline(hm[2])}</h${Math.min(6, hm[1].length + 2)}>`; continue; }
      const ul = l.match(/^\s*[-*•]\s+(.*)$/);
      const ol = l.match(/^\s*\d+[.)]\s+(.*)$/);
      if (ul || ol) {
        const want = ul ? 'ul' : 'ol';
        if (listType !== want) { closeList(); out += `<${want}>`; listType = want; }
        out += `<li>${inline((ul || ol)[1])}</li>`;
        continue;
      }
      if (!l.trim()) { closeList(); continue; }
      closeList();
      out += `<p>${inline(l)}</p>`;
    }
    closeList();
    if (inCode) out += '</code></pre>';
    return out;
  }

  return { esc, num, slug, titleCase, h, defer, pie, bar, line, table, chartTableCard, modal, toast, markdown, PALETTE };
})();
