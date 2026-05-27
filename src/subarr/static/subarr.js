// Subarr frontend — vanilla JS, no build step.
// Polls /api/queue + /api/gpu + /api/container on a 2s tick while Monitor /
// header is visible; opens an SSE stream for logs only when the Logs tab is
// active. Folder tree lazy-loads via /api/browse on each <details> expand.

(() => {
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));

  // ───── tab routing ─────
  function activate(tab) {
    $$('.tab').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    $$('.tabpanel').forEach((p) => p.classList.toggle('active', p.id === `tab-${tab}`));
    if (tab === 'logs') startLogs(); else stopLogs();
    if (tab === 'monitor') refreshMonitor();
    if (tab === 'settings') { loadSettings(); loadIntegrations(); }
    if (tab === 'coverage') loadCoverage();
  }
  $$('.tab').forEach((b) => b.addEventListener('click', () => activate(b.dataset.tab)));

  // ───── header poller (queue + gpu + container) ─────
  let headerTimer = null;
  async function pollHeader() {
    try {
      const [h, q, g, c] = await Promise.all([
        fetch('/api/health').then((r) => r.json()).catch(() => null),
        fetch('/api/queue').then((r) => (r.ok ? r.json() : null)).catch(() => null),
        fetch('/api/gpu').then((r) => r.json()).catch(() => null),
        fetch('/api/container').then((r) => (r.ok ? r.json() : null)).catch(() => null),
      ]);
      if (h) $('#version').textContent = `v${h.version}`;
      $('#container-status').textContent = c
        ? (c.running ? 'running' : (c.status || 'down'))
        : '—';
      $('#queue-summary').textContent = q
        ? `${q.processing_count} active · ${q.queued_count} queued`
        : 'unreachable';
      if (g?.online) {
        const used = g.memory.used_mib;
        const total = g.memory.total_mib;
        $('#gpu-summary').textContent = `${Math.round(used)}/${Math.round(total)} MiB · ${Math.round(g.utilization.gpu_pct)}%`;
      } else {
        $('#gpu-summary').textContent = g?.error ? '—' : '…';
      }
      if (window._currentTab === 'monitor') renderMonitor(g, c, q);
    } catch (e) {
      console.warn('header poll error', e);
    }
  }
  function startHeader() { if (!headerTimer) { pollHeader(); headerTimer = setInterval(pollHeader, 2000); } }

  // ───── Folder tree ─────
  async function browse(path) {
    const r = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
    if (!r.ok) throw new Error(`browse ${path} → ${r.status}`);
    return r.json();
  }

  const selectedPaths = new Set();

  function renderFileLeaf(entry) {
    // Video file row — flat checkbox + name + size; no <details>.
    const row = document.createElement('div');
    row.className = 'file-row' + (entry.has_sibling_srt ? ' has-srt' : '');
    row.dataset.path = entry.path;
    row.dataset.searchKey = entry.name.toLowerCase();
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = selectedPaths.has(entry.path);
    cb.addEventListener('change', () => {
      if (cb.checked) selectedPaths.add(entry.path);
      else selectedPaths.delete(entry.path);
      renderSelectedList();
    });
    const icon = document.createElement('span');
    icon.className = 'icon';
    icon.textContent = entry.has_sibling_srt ? '◉' : '▸';
    icon.title = entry.has_sibling_srt ? 'has sibling .srt already' : 'no sibling .srt';
    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = entry.name;
    row.appendChild(cb);
    row.appendChild(icon);
    row.appendChild(name);
    if (entry.size_mb != null) {
      const size = document.createElement('span');
      size.className = 'size';
      size.textContent = `${entry.size_mb} MB`;
      row.appendChild(size);
    }
    return row;
  }

  function renderEntry(entry) {
    if (!entry.is_dir) return renderFileLeaf(entry);

    const item = document.createElement('details');
    item.dataset.path = entry.path;
    item.dataset.searchKey = entry.name.toLowerCase();
    const summary = document.createElement('summary');
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = selectedPaths.has(entry.path);
    cb.addEventListener('click', (e) => e.stopPropagation());
    cb.addEventListener('change', () => {
      if (cb.checked) selectedPaths.add(entry.path);
      else selectedPaths.delete(entry.path);
      renderSelectedList();
    });
    cb.addEventListener('change', () => {
      // Cascade to children that are currently rendered. Children loaded
      // later (after an expand) won't auto-tick — that's intentional, so
      // ticking a parent doesn't silently include thousands of unseen files.
      const childCheckboxes = item.querySelectorAll('details input[type="checkbox"], .file-row input[type="checkbox"]');
      childCheckboxes.forEach((child) => {
        if (child === cb) return;
        if (child.checked !== cb.checked) {
          child.checked = cb.checked;
          child.dispatchEvent(new Event('change'));
        }
      });
    });
    summary.appendChild(cb);

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = entry.name;
    summary.appendChild(name);

    const badges = document.createElement('span');
    badges.className = 'badges';
    if (entry.video_count > 0) {
      const b = document.createElement('span');
      b.className = 'badge-vid';
      b.textContent = `${entry.video_count}v`;
      b.title = `${entry.video_count} video file${entry.video_count === 1 ? '' : 's'} in this folder`;
      badges.appendChild(b);
    }
    if (entry.srt_count > 0) {
      if (badges.children.length) badges.appendChild(document.createTextNode(' '));
      const b = document.createElement('span');
      b.className = 'badge-srt';
      b.textContent = `${entry.srt_count}srt`;
      b.title = `${entry.srt_count} existing .srt file${entry.srt_count === 1 ? '' : 's'} in this folder`;
      badges.appendChild(b);
    }
    summary.appendChild(badges);

    item.appendChild(summary);

    let loaded = false;
    item.addEventListener('toggle', async () => {
      if (!item.open || loaded) return;
      loaded = true;
      const inner = document.createElement('div');
      inner.textContent = 'loading…';
      inner.className = 'muted small';
      item.appendChild(inner);
      try {
        const data = await browse(entry.path);
        inner.innerHTML = '';
        inner.className = '';
        if (data.entries.length === 0) {
          const empty = document.createElement('div');
          empty.className = 'muted small';
          empty.textContent = '(empty)';
          inner.appendChild(empty);
        } else {
          for (const child of data.entries) inner.appendChild(renderEntry(child));
        }
        applyTreeFilter();
      } catch (e) {
        inner.innerHTML = '';
        inner.className = 'err small';
        inner.textContent = e.message;
      }
    });

    return item;
  }

  // ───── Tree filter ─────
  let treeFilter = '';
  function applyTreeFilter() {
    const f = treeFilter.toLowerCase();
    // Walk every node carrying a searchKey; hide rows that don't match and
    // (for dirs) don't have a matching descendant. Show ancestors of any
    // matching node so the path is preserved.
    const root = $('#tree-root');
    const nodes = root.querySelectorAll('[data-search-key]');
    if (!f) {
      nodes.forEach((n) => {
        n.classList.remove('hide-by-filter', 'match-by-filter');
      });
      return;
    }
    // Two passes: first mark direct matches, then propagate "visible" up
    // through ancestors. Anything not visible gets hidden.
    const visible = new Set();
    nodes.forEach((n) => {
      const direct = n.dataset.searchKey.includes(f);
      if (direct) {
        visible.add(n);
        // Mark ancestors visible.
        let p = n.parentElement;
        while (p && p !== root) {
          if (p.dataset && p.dataset.searchKey) visible.add(p);
          p = p.parentElement;
        }
        if (n.tagName === 'DETAILS') n.classList.add('match-by-filter');
      }
    });
    nodes.forEach((n) => {
      n.classList.toggle('hide-by-filter', !visible.has(n));
      if (visible.has(n) && n.tagName === 'DETAILS' && !n.dataset.searchKey.includes(f)) {
        // Show as ancestor — keep open so the match is reachable.
        n.open = true;
      }
    });
  }
  $('#tree-filter-input').addEventListener('input', (e) => {
    treeFilter = e.target.value.trim();
    applyTreeFilter();
  });
  $('#tree-clear-selection').addEventListener('click', () => {
    selectedPaths.clear();
    $$('.tree input[type="checkbox"]').forEach((cb) => (cb.checked = false));
    renderSelectedList();
  });

  async function loadRoot() {
    const root = $('#tree-root');
    root.textContent = 'loading…';
    try {
      const data = await browse('');
      root.innerHTML = '';
      if (data.entries.length === 0) {
        root.innerHTML = '<span class="muted">empty — check SUBARR_MEDIA_ROOT</span>';
        $('#tree-meta').textContent = '';
        return;
      }
      for (const entry of data.entries) root.appendChild(renderEntry(entry));
      const dirs = data.entries.filter((e) => e.is_dir).length;
      const files = data.entries.length - dirs;
      $('#tree-meta').textContent = `${dirs} folder${dirs === 1 ? '' : 's'}${files ? ` · ${files} file${files === 1 ? '' : 's'}` : ''}`;
      applyTreeFilter();
    } catch (e) {
      root.innerHTML = `<span class="err">${e.message}</span>`;
    }
  }
  $('#tree-refresh').addEventListener('click', loadRoot);

  function renderSelectedList() {
    const list = $('#scan-list');
    list.innerHTML = '';
    if (selectedPaths.size === 0) {
      const li = document.createElement('li');
      li.className = 'empty';
      li.textContent = 'No paths selected. Tick folders or files in the tree.';
      list.appendChild(li);
      $('#start-scan').disabled = true;
    } else {
      for (const p of selectedPaths) {
        const li = document.createElement('li');
        const span = document.createElement('span');
        span.className = 'path';
        span.textContent = p;
        const btn = document.createElement('button');
        btn.className = 'remove';
        btn.textContent = '✕';
        btn.addEventListener('click', () => {
          selectedPaths.delete(p);
          renderSelectedList();
          // Also uncheck the matching tree checkbox if visible.
          for (const cb of $$('.tree input[type="checkbox"]')) {
            const ent = cb.closest('details');
            if (ent && ent.querySelector('.name')?.textContent === p.split('/').pop()) {
              // best-effort — exact path match across multiple subtrees is racy
            }
          }
        });
        li.appendChild(span);
        li.appendChild(btn);
        list.appendChild(li);
      }
      $('#start-scan').disabled = false;
    }
    $('#queue-count').textContent = `${selectedPaths.size} path${selectedPaths.size === 1 ? '' : 's'}`;
  }
  renderSelectedList();

  // ───── Start scan + SSE progress ─────
  let activeScanSrc = null;
  function setActiveScanResults(scan) {
    const ul = $('#active-scan-results');
    ul.innerHTML = '';
    for (const r of scan.results) {
      const li = document.createElement('li');
      const status = document.createElement('span');
      status.className = `res-status ${r.status}`;
      status.textContent = r.status;
      const path = document.createElement('span');
      path.textContent = r.path;
      li.appendChild(status);
      li.appendChild(path);
      ul.appendChild(li);
    }
    if (scan.current_index < scan.paths.length && scan.status === 'running') {
      $('#active-scan-current').textContent = `running: ${scan.paths[scan.current_index]}`;
    } else if (scan.status === 'done') {
      $('#active-scan-current').textContent = `done · ${scan.paths.length} paths`;
    } else if (scan.status === 'error') {
      $('#active-scan-current').textContent = `error · see results`;
    }
  }

  async function startScanFlow() {
    const paths = [...selectedPaths];
    const reverse = $('#opt-reverse').checked;
    $('#start-scan').disabled = true;
    try {
      const r = await fetch('/api/scan', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ paths, reverse }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `scan failed: ${r.status}`);
      }
      const scan = await r.json();
      $('#active-scan').hidden = false;
      $('#active-scan-id').textContent = scan.id;
      $('#active-scan-current').textContent = 'starting…';
      if (activeScanSrc) activeScanSrc.close();
      activeScanSrc = new EventSource(`/api/scan/${scan.id}/events`);
      activeScanSrc.addEventListener('snapshot', (e) => setActiveScanResults(JSON.parse(e.data)));
      activeScanSrc.addEventListener('path_start', (e) => {
        const d = JSON.parse(e.data);
        $('#active-scan-current').textContent = `running [${d.index + 1}]: ${d.path}`;
      });
      activeScanSrc.addEventListener('path_done', async () => {
        const cur = await fetch(`/api/scan/${scan.id}`).then((r) => r.json());
        setActiveScanResults(cur);
      });
      activeScanSrc.addEventListener('done', (e) => {
        setActiveScanResults(JSON.parse(e.data));
        activeScanSrc.close();
        activeScanSrc = null;
        $('#start-scan').disabled = false;
      });
      activeScanSrc.addEventListener('error', () => {
        $('#start-scan').disabled = false;
      });
    } catch (e) {
      alert(e.message);
      $('#start-scan').disabled = false;
    }
  }
  $('#start-scan').addEventListener('click', startScanFlow);

  // ───── Logs SSE ─────
  let logsSrc = null;
  let logsPaused = false;
  let logFilter = '';
  const LOG_MAX = 2000;

  function classifyLog(line) {
    if (/ERROR|Failed|Traceback/.test(line)) return 'line-error';
    if (/WARN/.test(line)) return 'line-warn';
    if (/DEBUG/.test(line)) return 'line-debug';
    return 'line-info';
  }
  function startLogs() {
    if (logsSrc) return;
    $('#log-status').textContent = 'connecting…';
    logsSrc = new EventSource('/api/logs/events?tail=200');
    logsSrc.addEventListener('log', (e) => {
      if (logsPaused) return;
      const line = JSON.parse(e.data);
      if (logFilter && !line.toLowerCase().includes(logFilter)) return;
      const pre = $('#logs');
      const span = document.createElement('span');
      span.className = classifyLog(line);
      span.textContent = line + '\n';
      pre.appendChild(span);
      while (pre.childNodes.length > LOG_MAX) pre.removeChild(pre.firstChild);
      if ($('#log-autoscroll').checked) pre.scrollTop = pre.scrollHeight;
      $('#log-status').textContent = 'streaming';
    });
    logsSrc.addEventListener('error', () => {
      $('#log-status').textContent = 'error (will retry)';
    });
  }
  function stopLogs() {
    if (logsSrc) { logsSrc.close(); logsSrc = null; $('#log-status').textContent = 'disconnected'; }
  }
  $('#log-pause').addEventListener('click', () => {
    logsPaused = !logsPaused;
    $('#log-pause').textContent = logsPaused ? 'Resume' : 'Pause';
  });
  $('#log-clear').addEventListener('click', () => { $('#logs').innerHTML = ''; });
  $('#log-filter').addEventListener('input', (e) => { logFilter = e.target.value.toLowerCase(); });

  // ───── Monitor ─────
  function renderMonitor(g, c, q) {
    // GPU
    const gpuCard = $('#gpu-card');
    if (g?.online) {
      const memPct = g.memory.total_mib > 0 ? (g.memory.used_mib / g.memory.total_mib) * 100 : 0;
      gpuCard.innerHTML = `
        <div class="row"><span class="k">name</span><span class="v">${escape(g.name || '')}</span></div>
        <div class="row"><span class="k">vram</span><span class="v">${Math.round(g.memory.used_mib)} / ${Math.round(g.memory.total_mib)} MiB</span></div>
        <div class="bar"><div class="fill" style="width: ${memPct.toFixed(1)}%"></div></div>
        <div class="row"><span class="k">util</span><span class="v">${g.utilization.gpu_pct ?? '—'}%</span></div>
        <div class="row"><span class="k">temp</span><span class="v">${g.temperature_c ?? '—'} °C</span></div>
        <div class="row"><span class="k">power</span><span class="v">${g.power.draw_w ?? '—'} / ${g.power.limit_w ?? '—'} W</span></div>
        <div class="row"><span class="k">processes</span><span class="v">${g.processes.length} CUDA app(s)</span></div>
      `;
    } else {
      gpuCard.innerHTML = `<span class="muted">offline — ${escape(g?.error || '')}</span>`;
    }
    // Container
    const cCard = $('#container-card');
    if (c) {
      cCard.innerHTML = `
        <div class="row"><span class="k">name</span><span class="v">${escape(c.name || '')}</span></div>
        <div class="row"><span class="k">status</span><span class="v">${escape(c.status || '')}</span></div>
        <div class="row"><span class="k">started</span><span class="v">${escape(c.started_at || '')}</span></div>
        <div class="row"><span class="k">image</span><span class="v">${escape(c.image || '')}</span></div>
        <div class="row"><span class="k">id</span><span class="v">${escape(c.id_short || '')}</span></div>
      `;
    } else {
      cCard.innerHTML = `<span class="muted">docker unavailable</span>`;
    }
    // Queue (board layout, processing rows get progress bars)
    const qCard = $('#queue-card');
    const qProc = $('#q-processing');
    const qQueued = $('#q-queued');
    if (q) {
      qCard.innerHTML = `
        <div class="row"><span class="k">idle</span><span class="v">${q.idle ? 'yes' : 'no'}</span></div>
        <div class="row"><span class="k">queued</span><span class="v">${q.queued_count}</span></div>
        <div class="row"><span class="k">processing</span><span class="v">${q.processing_count}</span></div>
        <div class="row"><span class="k">version</span><span class="v">${escape(q.version || '')}</span></div>
      `;
      qProc.innerHTML = '';
      qQueued.innerHTML = '';
      if ((q.processing || []).length === 0) {
        qProc.innerHTML = '<li class="q-empty">idle</li>';
      } else {
        for (const t of q.processing) qProc.appendChild(renderQueueRow(t, 'processing'));
      }
      if ((q.queued || []).length === 0) {
        qQueued.innerHTML = '<li class="q-empty">empty</li>';
      } else {
        for (const t of q.queued) qQueued.appendChild(renderQueueRow(t, 'queued'));
      }
    } else {
      qCard.innerHTML = `<span class="muted">subgen unreachable</span>`;
      qProc.innerHTML = '';
      qQueued.innerHTML = '';
    }
  }

  function renderQueueRow(task, state) {
    const li = document.createElement('li');
    const basename = (task.path || '').split('/').pop() || task.path || '(unknown)';
    const head = document.createElement('div');
    head.className = 'q-row-head';
    head.innerHTML = `
      <span class="q-row-name" title="${escape(task.path || '')}">${escape(basename)}</span>
      <span class="q-row-type">${escape(task.type || '')}</span>
      <button class="q-cancel" disabled title="subgen v4.2 doesn't expose task cancel — needs a v4.3 patch">cancel</button>
    `;
    li.appendChild(head);
    if (state === 'processing' && task.progress) {
      const p = task.progress;
      const line = document.createElement('div');
      line.className = 'q-progress-line';
      line.innerHTML = `<span>${p.pct}%</span><span>${p.elapsed} elapsed · ${p.eta} eta · ${p.speed_s_per_s.toFixed(2)}s/s</span>`;
      li.appendChild(line);
      const bar = document.createElement('div');
      bar.className = 'q-progress-bar';
      bar.innerHTML = `<div class="fill" style="width: ${p.pct}%"></div>`;
      li.appendChild(bar);
    } else if (state === 'processing') {
      const line = document.createElement('div');
      line.className = 'q-progress-line';
      line.innerHTML = `<span class="muted">no progress data yet</span>`;
      li.appendChild(line);
    }
    return li;
  }
  function refreshMonitor() { pollHeader(); /* renderMonitor runs from pollHeader */ }

  // ───── Settings ─────
  async function loadSettings() {
    try {
      const data = await fetch('/api/mode').then((r) => r.json());
      $('#settings-compose-path').textContent = data.compose_path || '';
      const tbody = $('#kwargs-table tbody');
      tbody.innerHTML = '';
      for (const lk of data.per_language_kwargs || []) {
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${lk.code}</td><td>${escape(lk.parse_error ? lk.raw + '  /* ' + lk.parse_error + ' */' : JSON.stringify(lk.parsed))}</td>`;
        tbody.appendChild(tr);
      }
      $('#kwargs-top').textContent = data.top_level_kwargs
        ? JSON.stringify(data.top_level_kwargs, null, 2)
        : (data.top_level_raw || '(none)');
    } catch (e) {
      $('#settings-compose-path').textContent = `error: ${e.message}`;
    }
  }

  $('#btn-restart').addEventListener('click', async () => {
    if (!confirm('Restart the subgen container? In-flight transcribes will be lost.')) return;
    $('#action-result').textContent = 'restarting…';
    try {
      const r = await fetch('/api/restart', { method: 'POST' });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || `restart failed: ${r.status}`);
      $('#action-result').textContent = `restarted: ${body.container?.id_short || '?'}`;
    } catch (e) {
      $('#action-result').textContent = `restart failed: ${e.message}`;
    }
  });
  $('#btn-plex-scan').addEventListener('click', async () => {
    $('#action-result').textContent = 'triggering plex scan…';
    try {
      const r = await fetch('/api/plex/scan', { method: 'POST' });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || `plex scan failed: ${r.status}`);
      $('#action-result').textContent = `plex scan triggered (section: ${body.section})`;
    } catch (e) {
      $('#action-result').textContent = `plex scan failed: ${e.message}`;
    }
  });

  // ───── Integrations health (Settings tab) ─────
  async function loadIntegrations() {
    const grid = $('#integrations-grid');
    grid.textContent = 'loading…';
    try {
      const data = await fetch('/api/integrations/health').then((r) => r.json());
      grid.innerHTML = '';
      for (const it of (data.integrations || [])) {
        const cell = document.createElement('div');
        cell.className = 'integration';
        const dotCls = !it.configured ? 'unconf' : (it.online ? 'up' : 'down');
        const statusLabel = !it.configured ? 'not configured' : (it.online ? 'online' : 'offline');
        let metaHtml = `<div class="meta">${escape(statusLabel)}${it.version ? ' · ' + escape(it.version) : ''}</div>`;
        if (it.badges) {
          const b = it.badges;
          metaHtml += `<div class="meta">eps wanted: ${b.episodes ?? '?'} · movies: ${b.movies ?? '?'} · providers: ${b.providers ?? '?'}</div>`;
        }
        if (it.error) {
          metaHtml += `<div class="err">${escape(it.error)}</div>`;
        }
        cell.innerHTML = `<div class="name"><span class="dot ${dotCls}"></span>${escape(it.name)}</div>${metaHtml}`;
        grid.appendChild(cell);
      }
    } catch (e) {
      grid.innerHTML = `<span class="err">${escape(e.message)}</span>`;
    }
  }

  // ───── Coverage tab ─────
  let coverageRaw = null;

  // Track rows queued in this browser across page reloads. Keyed by:
  //   ep:<sonarr_episode_id>  for episodes
  //   mv:<canonical_path>     for movies
  // Value: { scan_id, queued_at_ms }. We forget entries older than 24h
  // to avoid stale-state confusion across days.
  const COV_QUEUED_KEY = 'subarr.coverage.queued.v1';
  function loadQueuedMap() {
    try {
      const raw = JSON.parse(localStorage.getItem(COV_QUEUED_KEY) || '{}');
      const now = Date.now();
      const fresh = {};
      for (const [k, v] of Object.entries(raw)) {
        if (v && v.queued_at_ms && (now - v.queued_at_ms) < 24 * 3600 * 1000) fresh[k] = v;
      }
      return fresh;
    } catch { return {}; }
  }
  function saveQueuedMap(m) {
    try { localStorage.setItem(COV_QUEUED_KEY, JSON.stringify(m)); } catch {}
  }
  function coverageRowKey(item) {
    const eid = item?.bazarr?.episode_id;
    if (eid) return `ep:${eid}`;
    if (item?.canonical_path) return `mv:${item.canonical_path}`;
    return null;
  }
  function renderCoverage() {
    if (!coverageRaw) return;
    const tbody = $('#cov-table tbody');
    tbody.innerHTML = '';
    var covQueuedMap = loadQueuedMap();
    const hideStale = $('#cov-hide-stale').checked;
    const filter = ($('#cov-filter').value || '').toLowerCase().trim();
    let shown = 0;
    for (const item of coverageRaw.items) {
      if (hideStale && item.has_sub_on_disk) continue;
      if (filter) {
        const hay = [item.title, item.episode_title, item.original_language, ...(item.tags || [])]
          .filter(Boolean).join(' ').toLowerCase();
        if (!hay.includes(filter)) continue;
      }
      shown++;
      const tr = document.createElement('tr');
      if (item.has_sub_on_disk) tr.classList.add('row-stale');
      const scoreCls = item.score >= 1000 ? 'high' : item.score >= 200 ? 'mid' : item.score < 0 ? 'neg' : 'low';
      const rowKey = coverageRowKey(item);
      const isAlreadyQueued = rowKey && (covQueuedMap[rowKey] !== undefined);
      if (isAlreadyQueued) tr.classList.add('row-queued');
      const canQueue = !!(item.bazarr?.episode_id) || !!item.canonical_path;
      let queueBtnLabel;
      let queueBtnTitle;
      if (isAlreadyQueued) {
        queueBtnLabel = '✓ queued';
        queueBtnTitle = `scan_id ${covQueuedMap[rowKey].scan_id} — queued earlier in this browser`;
      } else if (item.has_sub_on_disk) {
        queueBtnLabel = 'Re-scan';
        queueBtnTitle = 'Re-queue this file (existing .srt will be overwritten by subgen)';
      } else {
        queueBtnLabel = '→ Queue';
        queueBtnTitle = item.bazarr?.episode_id
          ? `Resolve sonarr episode ${item.bazarr.episode_id} → single .mkv and send to subgen`
          : 'Send this row to subgen (path-level)';
      }
      if (!canQueue) queueBtnTitle = 'No sonarr episode id or canonical path — nothing to queue';
      tr.dataset.rowKey = rowKey || '';
      tr.innerHTML = `
        <td class="score ${scoreCls}" title="${escape((item.score_reasons || []).join(' · '))}">${item.score}</td>
        <td>${escape(item.media_type)}</td>
        <td>${escape(item.title)}</td>
        <td>${escape(item.episode_number ? item.episode_number + ' ' : '')}${escape(item.episode_title || '')}</td>
        <td class="lang">${escape(item.original_language || '—')}</td>
        <td>${item.monitored === null ? '—' : (item.monitored ? '✓' : '✗')}</td>
        <td class="${item.has_sub_on_disk ? 'disk-srt-yes' : 'disk-srt-no'}" title="${item.has_sub_on_disk ? 'Disk has .srt: Bazarr view is stale. Re-scan only if the existing sub is bad.' : ''}">${item.has_sub_on_disk ? '!' : '—'}</td>
        <td>${escape((item.bazarr?.missing_subtitles || []).join(', '))}</td>
        <td>${escape((item.tags || []).join(', '))}</td>
        <td><button class="ghost small cov-queue-btn ${isAlreadyQueued ? 'queued' : ''}"
                   data-episode-id="${item.bazarr?.episode_id ?? ''}"
                   data-canonical="${escape(item.canonical_path || '')}"
                   data-row-key="${escape(rowKey || '')}"
                   title="${escape(queueBtnTitle)}" ${(canQueue && !isAlreadyQueued) ? '' : 'disabled'}>${queueBtnLabel}</button></td>
      `;
      tbody.appendChild(tr);
    }
    if (shown === 0) {
      tbody.innerHTML = `<tr class="empty-row"><td colspan="9">no rows match (or coverage list is empty)</td></tr>`;
    }
    const t = coverageRaw.totals || {};
    const ts = coverageRaw.generated_at ? new Date(coverageRaw.generated_at * 1000).toLocaleTimeString() : '?';
    $('#coverage-meta').textContent = `${shown} shown · ${t.items} total (${t.episodes} ep + ${t.movies} mv) · ${t.with_disk_sub} with .srt on disk · generated ${ts}${coverageRaw.cached ? ` (cached ${coverageRaw.cache_age_s}s)` : ''}`;
  }

  function renderCoverageSources() {
    const wrap = $('#cov-sources');
    wrap.innerHTML = '';
    const s = coverageRaw?.sources || {};
    for (const [name, info] of Object.entries(s)) {
      const span = document.createElement('span');
      const cls = !info.configured ? 'unconf' : (info.ok ? 'ok' : 'err');
      span.className = `src ${cls}`;
      let label = `${name}: `;
      if (!info.configured) label += 'unconfigured';
      else if (info.ok) {
        const counts = [];
        if ('count' in info) counts.push(`${info.count} rows`);
        if ('episodes_wanted' in info) counts.push(`${info.episodes_wanted} eps wanted`);
        if ('movies_wanted' in info) counts.push(`${info.movies_wanted} movies wanted`);
        if ('history_rows' in info) counts.push(`${info.history_rows} history rows`);
        label += counts.length ? counts.join(', ') : 'ok';
      } else label += info.error || 'error';
      span.textContent = label;
      wrap.appendChild(span);
    }
  }

  async function loadCoverage(fresh = false) {
    const meta = $('#coverage-meta');
    meta.textContent = fresh ? 'refreshing (this can take ~10s on first run)…' : 'loading…';
    try {
      const useTautulli = $('#cov-tautulli').checked;
      const url = `/api/coverage?tautulli=${useTautulli}${fresh ? '&fresh=true' : ''}`;
      const r = await fetch(url);
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `coverage failed: ${r.status}`);
      }
      coverageRaw = await r.json();
      renderCoverageSources();
      renderCoverage();
    } catch (e) {
      meta.textContent = `error: ${e.message}`;
    }
  }
  $('#cov-refresh').addEventListener('click', () => loadCoverage(true));
  $('#cov-tautulli').addEventListener('change', () => loadCoverage(true));
  $('#cov-hide-stale').addEventListener('change', renderCoverage);
  $('#cov-filter').addEventListener('input', renderCoverage);

  // Per-row Queue button (event-delegated). Posts to /api/coverage/queue
  // which resolves the sonarr_episode_id to a single .mkv file before
  // enqueueing — so we queue ONE episode, not the whole series directory.
  $('#cov-table').addEventListener('click', async (ev) => {
    const btn = ev.target.closest('.cov-queue-btn');
    if (!btn || btn.disabled) return;
    const epId = btn.dataset.episodeId ? Number(btn.dataset.episodeId) : null;
    const canonical = btn.dataset.canonical || null;
    const rowKey = btn.dataset.rowKey || null;
    btn.disabled = true;
    btn.textContent = '…';
    try {
      const body = {};
      if (epId) body.sonarr_episode_id = epId;
      else if (canonical) body.canonical_path = canonical;
      else throw new Error('nothing to queue');
      const r = await fetch('/api/coverage/queue', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      // Persist + flip row to queued state.
      if (rowKey) {
        const map = loadQueuedMap();
        map[rowKey] = { scan_id: data.id, canonical: data.canonical_path, queued_at_ms: Date.now() };
        saveQueuedMap(map);
      }
      btn.textContent = '✓ queued';
      btn.classList.add('queued');
      btn.title = `scan_id ${data.id} · resolved ${data.canonical_path}`;
      const tr = btn.closest('tr');
      if (tr) tr.classList.add('row-queued');
    } catch (e) {
      btn.textContent = '✗ ' + (e.message.length > 40 ? e.message.slice(0, 40) + '…' : e.message);
      btn.title = e.message;
      setTimeout(() => { btn.textContent = '→ Queue'; btn.disabled = false; }, 6000);
    }
  });

  // ───── helpers ─────
  function escape(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ───── boot ─────
  window._currentTab = 'scan';
  document.addEventListener('click', (e) => {
    const t = e.target.closest('.tab');
    if (t) window._currentTab = t.dataset.tab;
  });
  startHeader();
  loadRoot();
})();
